"""Quick verification: demo_02 core logic test"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_common import read_json, add_project_to_sys_path
add_project_to_sys_path(__file__)

# Test 1: demo_02 — parse swagger
from demo_02_文档解析.core import parse_swagger_document, detect_format, parse_document

swagger = read_json("sample_swagger.json")
fmt = detect_format(swagger)
print(f"Format detected: {fmt}")
ifaces = parse_swagger_document(swagger)
print(f"Interfaces parsed: {len(ifaces)}")
for i in ifaces[:3]:
    print(f"  {i['method']} {i['path']} - {i['name']}")

# Test format detection
assert detect_format({"info": {"schema": "https://schema.getpostman.com/"}, "item": [{"request": {}}]}) == "postman"
assert detect_format("curl https://api.example.com/login") == "curl"
assert detect_format({"apiCollection": []}) == "apifox"
assert detect_format({"openapi": "3.0", "paths": {}}) == "swagger"
assert detect_format({"log": {"entries": [{"request": {}}]}}) == "har"
print("Format detection: all 5 correct")

# Test 2: demo_04 — dependency analysis
from demo_04_依赖分析.core import analyze_dependencies, GROUP_KEYWORDS, generate_test_scenarios

assert len(GROUP_KEYWORDS) == 32, f"Expected 32 groups, got {len(GROUP_KEYWORDS)}"
print(f"GROUP_KEYWORDS: 32 groups (correct)")

deps_result = analyze_dependencies(ifaces)
print(f"Dependency analysis: {len(deps_result['execution_order'])} step execution order")
print(f"Groups: {deps_result['groups'].get('total_groups', 0)} groups")

scenarios = generate_test_scenarios(deps_result, max_scenarios=5)
print(f"Generated scenarios: {len(scenarios)}")

# Test 3: demo_05 — data generation
from demo_05_数据工厂.core import generate_test_data, AdvancedDataGenerator, generate_test_data_for_api
td = generate_test_data(ifaces, deps_result)
print(f"Test data: {td['total_cases']} cases across {td['total_interfaces']} interfaces")

# Test AdvancedDataGenerator
gen = AdvancedDataGenerator(ifaces, deps_result)
api1_cases = generate_test_data_for_api(ifaces[0], ["positive", "boundary"])
print(f"generate_test_data_for_api: {len(api1_cases['cases'])} cases with {len(api1_cases['pipeline_log'])} pipeline steps")

# Test 4: demo_06 — case generation
from demo_06_用例生成.core import generate_test_cases, DEFAULT_FEW_SHOT, expand_schema_4_levels
cases = generate_test_cases(ifaces, td["test_data"])
print(f"Test cases generated: {cases['total_cases']} ({cases['generation_mode']} mode)")
print(f"Few-shot examples: {len(DEFAULT_FEW_SHOT)}")
assert len(DEFAULT_FEW_SHOT) == 5

# Test schema expansion
expanded = expand_schema_4_levels({"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}})
assert expanded["properties"]["name"]["type"] == "string"
print("expand_schema_4_levels: OK")

# Test 5: demo_09 — execution
from demo_09_异步执行.core import execute_test_cases, substitute_variables, _execute_with_retry, MockResponder
exec_result = execute_test_cases(cases["test_cases"], concurrency=3)
print(f"Execution: {exec_result['total']} total, {exec_result['passed_count']} pass, {exec_result['failed_count']} fail (rate {exec_result['pass_rate']}%)")

# Test variable substitution
result = substitute_variables({"key": "{{token}}"}, {"token": "abc123"})
assert result == {"key": "abc123"}, f"Expected {{'key': 'abc123'}}, got {result}"
result2 = substitute_variables({"key": "${id}"}, {"id": 42})
assert result2 == {"key": "42"}
print("substitute_variables: OK ({{}} and ${} formats)")

# Test 6: demo_10 — failure analysis
from demo_10_失败分析.core import analyze_failures, detect_schema_changes, AISuggestionService
analysis = analyze_failures(exec_result)
print(f"Failure analysis: {analysis['total_failures']} failures, top category: {analysis['summary'].get('top_category')}")

# Test schema change detection
changes = detect_schema_changes({"name": "string"}, {"name": "integer"})
assert changes["has_changes"] == True
print(f"detect_schema_changes: {changes['summary']}")

# Test AISuggestionService
svc = AISuggestionService(analysis.get("analyses", []))
suggestions = svc.generate_suggestions()
print(f"AISuggestionService: {len(suggestions)} dimensions of suggestions")

print("\n" + "=" * 60)
print("ALL VERIFICATION TESTS PASSED!")
print("=" * 60)
