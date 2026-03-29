import os
import tempfile
import logging
from pathlib import Path
from typing import List, Dict, Any

from src.matclaw.matlab.matlab_bridge import MatlabBridge, MatlabCallRequest

logger = logging.getLogger(__name__)

class StaticAnalyzer:
    """
    Implements a wrapper around MATLAB's checkcode (mlint) to catch syntax and undefined variable
    errors before execution, and checks installed toolboxes to prevent model hallucinations.
    """

    def __init__(self, matlab_bridge: MatlabBridge):
        self.bridge = matlab_bridge

    def get_installed_toolboxes(self) -> List[str]:
        """Returns a list of installed MATLAB toolboxes using the 'ver' command."""
        if not self.bridge.is_healthy():
            logger.warning("MATLAB bridge not healthy; cannot retrieve toolboxes.")
            return []

        # {ver().Name} returns a cell array of strings in MATLAB
        req = MatlabCallRequest(function="eval", args=["{ver().Name}"], nargout=1)
        res = self.bridge.call(req)
        
        if res.success and res.result:
            try:
                # Convert the MATLAB cell array (which comes through as a list) to strings
                return [str(x) for x in res.result]
            except Exception as e:
                logger.error(f"Failed to parse toolboxes: {e}")
                return []
        
        return []

    def check_code(self, source_code: str) -> List[Dict[str, Any]]:
        """
        Runs mlint (checkcode) on the provided MATLAB source code.
        Returns a list of warnings/errors to be fed back to the LLM before actual execution.
        """
        if not self.bridge.is_healthy():
            logger.warning("MATLAB bridge not healthy; skipping static analysis.")
            return [{"message": "MATLAB bridge not healthy, skipping static analysis."}]
        
        # checkcode requires a file
        with tempfile.NamedTemporaryFile(suffix=".m", delete=False, mode="w") as tmp:
            tmp.write(source_code)
            tmp_path = tmp.name

        try:
            # Use '-string' flag so checkcode returns a char vector, not a struct array.
            # The Python MATLAB engine cannot return non-scalar struct arrays.
            escaped = tmp_path.replace("'", "''")
            req = MatlabCallRequest(
                function="eval",
                args=[f"checkcode('{escaped}', '-string')"],
                nargout=1,
            )
            res = self.bridge.call(req)

            if res.success and res.result is not None:
                text = str(res.result).strip()
                if not text:
                    return []
                issues = []
                for line in text.splitlines():
                    line = line.strip()
                    if line:
                        issues.append({"message": line})
                return issues
            elif not res.success:
                # Treat as non-fatal — don't block execution for analysis failures
                logger.warning("checkcode skipped: %s", res.error)
                return []

            return []
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
