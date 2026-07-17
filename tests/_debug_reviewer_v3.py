"""Debug Reviewer _parse_review_result."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.prompts import extract_json
from core.reviewer import ReviewVerdict, ReviewResult, Severity, ReviewIssue

json_text = '{"verdict": "approved", "summary": "All criteria met.", "issues": [], "evidence": "Function returns int correctly."}'

# Step 1
parsed = extract_json(json_text)
with open("_debug_reviewer_out.txt", "w") as f:
    f.write(f"Step 1 - extract_json return type: {type(parsed).__name__}\n")
    f.write(f"Step 1 - extract_json return value: {parsed}\n")
    
    if isinstance(parsed, dict):
        f.write(f"Step 2 - verdict_raw from dict: '{parsed.get('verdict')}'\n")
        try:
            verdict = ReviewVerdict(parsed.get("verdict"))
            f.write(f"Step 2 - ReviewVerdict: {verdict}\n")
        except Exception as e:
            f.write(f"Step 2 - ERROR: {e}\n")
        
        try:
            result = ReviewResult.from_dict(parsed)
            f.write(f"Step 3 - ReviewResult.from_dict verdict: {result.verdict}\n")
            f.write(f"Step 3 - ReviewResult.from_dict passed: {result.passed}\n")
        except Exception as e:
            f.write(f"Step 3 - ERROR: {type(e).__name__}: {e}\n")
    
    result2 = ReviewResult._parse_review_result(json_text)
    f.write(f"Final - _parse_review_result verdict: {result2.verdict}\n")

print("Debug output written to _debug_reviewer_out.txt")
