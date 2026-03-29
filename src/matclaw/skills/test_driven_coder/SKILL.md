---
name: test_driven_coder
description: "Implements a strict TDD (Test-Driven Development) loop for writing MATLAB functions."
---

# Test-Driven Coder Skill

## Purpose
This skill forces the agent to break down a requested computation into a discrete `.m` function and an accompanying `*_test.m` file using `matlab.unittest`. This enforces the "Component Testing" mandate to prevent monolithic errors.

## RPI Loop Implementation
1. **Research**: Identifies the inputs and outputs needed for the requested sub-component.
2. **Plan**: Drafts the `matlab.unittest.TestCase` FIRST. Static Analyzer (`mlint`) checks the test for syntax errors.
3. **Execute**: 
   - Write the test file `*_test.m`.
   - Write the implementation file `*.m`.
   - Run `MatlabBridge.run_unittest()` on the test file.
   - If it fails, capture the exception, feed it to the LLM, and loop back to execution until passing.
