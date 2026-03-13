#!/usr/bin/env python3
"""Run all MatClaw tests in one go. No manual steps required."""

import sys
import os

# Ensure project root
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())

class SkipTest(Exception):
    pass

def run(name: str, fn):
    try:
        fn()
        print(f"  ✓ {name}")
        return True
    except SkipTest as e:
        print(f"  ⊘ {name}: {e}")
        return True  # skipped counts as ok
    except Exception as e:
        print(f"  ✗ {name}: {e}")
        return False

def test_matlab_bridge():
    import matlab
    from src.matclaw.matlab.matlab_bridge import MatlabBridge, MatlabCallRequest
    from src.matclaw.config.base_config import MatClawSettings
    s = MatClawSettings()
    b = MatlabBridge(settings=s.matlab)
    b.start()
    r = b.call(MatlabCallRequest(function='version', args=[], nargout=1))
    assert r.success, r.error
    coeffs = matlab.double([1.0, -5.0, 6.0])
    r = b.call(MatlabCallRequest(function='roots', args=[coeffs], nargout=1))
    assert r.success, r.error
    b.stop()

def test_workspace_auditor():
    from src.matclaw.matlab.matlab_bridge import MatlabBridge
    from src.matclaw.config.base_config import MatClawSettings
    from src.matclaw.skills.workspace_auditor.logic import run as run_skill
    s = MatClawSettings()
    b = MatlabBridge(settings=s.matlab)
    b.start()
    result = run_skill(b)
    assert result.success, result.error
    b.stop()

def test_nl_router():
    from dotenv import load_dotenv
    load_dotenv()
    if not (os.getenv('NVIDIA_API_KEY') or os.getenv('GOOGLE_API_KEY')):
        raise SkipTest('No LLM API key (NVIDIA_API_KEY or GOOGLE_API_KEY)')
    try:
        from src.matclaw.gateways.nl_router import route_nl_message
        r = route_nl_message('Check my workspace', [], ['workspace_auditor', 'pid_optimizer'])
        assert r.intent in ('run_skill', 'execute_code', 'ask_question')
    except Exception as e:
        if 'quota' in str(e).lower() or '429' in str(e):
            raise SkipTest('API quota exceeded') from e
        raise

def test_mcp_tools():
    from src.matclaw.skills import list_skills
    skills = list_skills()
    assert isinstance(skills, list) and len(skills) > 0

def main():
    print("MatClaw automated tests\n" + "=" * 40)
    tests = [
        ("MATLAB bridge (version + roots)", test_matlab_bridge),
        ("Workspace auditor skill", test_workspace_auditor),
        ("NL router (intent)", test_nl_router),
        ("MCP tools (list skills)", test_mcp_tools),
    ]
    ok = 0
    for name, fn in tests:
        if run(name, fn):
            ok += 1
    print("=" * 40)
    print(f"Passed: {ok}/{len(tests)}")
    sys.exit(0 if ok == len(tests) else 1)

if __name__ == "__main__":
    main()
