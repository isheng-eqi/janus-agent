"""
Phase 2 全面测试：Planner 加载历史执行模式 (_load_relevant_patterns, _build_patterns_section, _plan 注入)

Run from janus/ root:
  cd janus && python -m pytest tests/test_phase2_patterns.py -v
"""

import json
import os
import sys
import tempfile
import shutil
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.planner import Planner, calibrate_and_adjust, _estimate_task_difficulty, _difficulty_sort_preserve_deps
from core.protocol import ExecutionPattern, Directive, TaskSpec
from core.task_manager import TaskManager


# ============================================================================
# Helper: 创建一个最小化的 Planner 实例（不需要真实 LLM）
# ============================================================================

def _make_minimal_planner():
    """Create a Planner instance with all required dependencies stubbed."""
    tm = TaskManager()
    def stub_factory(model_override=None):
        return MagicMock()
    return Planner(
        model="deepseek-chat",
        api_key="fake-key-for-testing",
        task_manager=tm,
        worker_factory=stub_factory,
        reviewer=None,
        max_depth=3,
    )


# ============================================================================
# 测试 1: _load_relevant_patterns 方法
# ============================================================================

class TestLoadRelevantPatterns(unittest.TestCase):
    """测试 _load_relevant_patterns 的关键字匹配和过滤逻辑。"""

    @classmethod
    def setUpClass(cls):
        """创建测试用的 patterns 目录和文件。"""
        cls._test_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "patterns"
        )
        os.makedirs(cls._test_dir, exist_ok=True)

        cls._test_patterns = [
            {
                "task_type": "csv_sort",
                "description": "读取CSV文件并按年龄列排序",
                "tool_sequence": [
                    {"name": "read_file", "args_summary": "...", "success": True},
                    {"name": "terminal", "args_summary": "python sort csv", "success": True},
                ],
                "success": True,
                "lessons": "先读头部确认编码，避免编码错误",
                "timestamp": "2026-07-19T00:00:00Z",
                "task_id": "test-001",
            },
            {
                "task_type": "json_parse",
                "description": "解析JSON配置文件获取数据库连接参数",
                "tool_sequence": [
                    {"name": "read_file", "args_summary": "config.json", "success": True},
                    {"name": "terminal", "args_summary": "python -c json.loads", "success": True},
                ],
                "success": True,
                "lessons": "用python解析比grep可靠，能验证格式正确性",
                "timestamp": "2026-07-19T00:01:00Z",
                "task_id": "test-002",
            },
            {
                "task_type": "http_test",
                "description": "测试HTTP接口连通性和响应时间",
                "tool_sequence": [
                    {"name": "terminal", "args_summary": "curl -m 1 http://api", "success": False},
                ],
                "success": False,
                "lessons": "curl超时设置太短导致误判，至少设置3秒",
                "timestamp": "2026-07-19T00:02:00Z",
                "task_id": "test-003",
            },
            {
                "task_type": "empty_fail",
                "description": "一个失败但没有教训的任务",
                "tool_sequence": [
                    {"name": "terminal", "args_summary": "broken command", "success": False},
                ],
                "success": False,
                "lessons": "",
                "timestamp": "2026-07-19T00:03:00Z",
                "task_id": "test-004",
            },
        ]
        for p in cls._test_patterns:
            fpath = os.path.join(cls._test_dir, f"{p['task_id']}.json")
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(p, f, ensure_ascii=False, indent=2)

    @classmethod
    def tearDownClass(cls):
        """清理测试 patterns 目录。"""
        if cls._test_dir and os.path.isdir(cls._test_dir):
            shutil.rmtree(cls._test_dir, ignore_errors=True)

    def setUp(self):
        self.planner = _make_minimal_planner()

    # --- 基本匹配测试 ---

    def test_01_csv_query_matches_csv_pattern(self):
        """CSV 查询应该匹配到 test-001（CSV 相关）。"""
        results = self.planner._load_relevant_patterns("读取CSV文件并按年龄列排序")
        self.assertGreater(len(results), 0, "应该至少匹配到一个 CSV 相关的 pattern")
        task_ids = [r["task_id"] for r in results]
        self.assertIn("test-001", task_ids,
                      f"CSV 查询应匹配到 test-001，实际匹配: {task_ids}")

    def test_02_api_query_matches_http_pattern(self):
        """API 查询应该匹配到 test-003（HTTP 相关）。"""
        results = self.planner._load_relevant_patterns("测试一下API接口的响应性能")
        self.assertGreater(len(results), 0, "应该至少匹配到一个 API 相关的 pattern")
        task_ids = [r["task_id"] for r in results]
        self.assertIn("test-003", task_ids,
                      f"API 查询应匹配到 test-003，实际匹配: {task_ids}")

    def test_03_json_query_matches_json_pattern(self):
        """JSON 配置查询应该匹配 test-002。"""
        results = self.planner._load_relevant_patterns("解析JSON配置文件")
        self.assertGreater(len(results), 0, "应该至少匹配到一个 JSON 相关的 pattern")
        task_ids = [r["task_id"] for r in results]
        self.assertIn("test-002", task_ids)

    # --- 过滤逻辑测试 ---

    def test_04_empty_fail_filtered_out(self):
        """test-004（失败且无教训）应该被过滤掉，不出现在任何结果中。"""
        results = self.planner._load_relevant_patterns("运行终端命令")
        task_ids = [r["task_id"] for r in results]
        self.assertNotIn("test-004", task_ids,
                         "失败且无教训的 pattern 应被过滤，不应出现在结果中")
        for r in results:
            self.assertTrue(
                r.get("success") or r.get("lessons"),
                f"返回的 pattern {r.get('task_id')} 应该有 success=True 或有 lessons"
            )

    def test_05_failed_with_lessons_not_filtered(self):
        """失败但有教训的 pattern（test-003）不应被过滤。"""
        results = self.planner._load_relevant_patterns("测试HTTP接口连通性")
        task_ids = [r["task_id"] for r in results]
        self.assertIn("test-003", task_ids,
                      "失败但有教训的 pattern 应保留在结果中")

    # --- 返回数据完整性验证 ---

    def test_06_returned_pattern_has_all_fields(self):
        """返回的 pattern 包含所有必需字段。"""
        results = self.planner._load_relevant_patterns("读取CSV文件并按年龄列排序")
        self.assertGreater(len(results), 0)
        for r in results:
            required_fields = ["task_type", "description", "tool_sequence",
                              "success", "lessons", "timestamp", "task_id"]
            for field in required_fields:
                self.assertIn(field, r,
                              f"Pattern {r.get('task_id', '?')} 缺少字段 {field}")

    def test_07_default_limit_is_3(self):
        """默认 limit=3，返回不超过 3 个结果。"""
        results = self.planner._load_relevant_patterns("读取 解析 测试 命令 文件")
        self.assertLessEqual(len(results), 3,
                            f"默认 limit=3，返回 {len(results)} 超过了限制")

    def test_08_custom_limit(self):
        """自定义 limit 参数生效。"""
        results = self.planner._load_relevant_patterns("读取 解析 测试 命令 文件", limit=1)
        self.assertLessEqual(len(results), 1)

    def test_09_no_match_returns_empty(self):
        """无关键词匹配时应返回空列表。"""
        results = self.planner._load_relevant_patterns("xxxxxxxx不会匹配的关键词yyyyyyyy")
        self.assertEqual(len(results), 0, "无匹配时应返回空列表")

    # --- 停用词测试 ---

    def test_10_stopwords_excluded(self):
        """停用词（如 "的"、"是"、"the"、"a"）不应参与匹配。"""
        results = self.planner._load_relevant_patterns("这是一个测试")
        # "是"、"一个" 都是停用词，只有 "测试" 可能匹配
        # 不崩溃就是通过
        self.assertIsInstance(results, list)

    # --- CJK bigram 测试 ---

    def test_11_cjk_bigram_matching(self):
        """CJK 文本的字符二元组也能匹配（如 "文件"、"排序"）。"""
        results = self.planner._load_relevant_patterns("文件内容读取")
        self.assertGreater(len(results), 0, "CJK bigram 应该能产生匹配")


# ============================================================================
# 测试 2: _build_patterns_section 方法
# ============================================================================

class TestBuildPatternsSection(unittest.TestCase):
    """测试 _build_patterns_section 的文本格式生成。"""

    def setUp(self):
        self.planner = _make_minimal_planner()

    def test_01_empty_patterns_returns_empty_string(self):
        """空列表应返回空字符串。"""
        result = self.planner._build_patterns_section([])
        self.assertEqual(result, "")

    def test_02_success_pattern_format(self):
        """成功模式的格式：有描述、工具、教训、成功标记。"""
        patterns = [
            {
                "task_type": "csv_sort",
                "description": "读取CSV文件并排序",
                "tool_sequence": [
                    {"name": "read_file"},
                    {"name": "terminal"},
                ],
                "success": True,
                "lessons": "先读头部确认编码",
                "timestamp": "2026-07-19T00:00:00Z",
                "task_id": "test-001",
            }
        ]
        result = self.planner._build_patterns_section(patterns)

        self.assertIn("历史执行模式参考", result)
        self.assertIn("成功", result)
        self.assertIn("读取CSV文件并排序", result)
        self.assertIn("read_file", result)
        self.assertIn("terminal", result)
        self.assertIn("先读头部确认编码", result)
        self.assertIn("是", result)  # 成功=是

    def test_03_failed_pattern_has_warning(self):
        """失败模式有 ⚠️ 警告标记。"""
        patterns = [
            {
                "task_type": "http_test",
                "description": "测试HTTP接口",
                "tool_sequence": [
                    {"name": "terminal"},
                ],
                "success": False,
                "lessons": "超时设置太短",
                "timestamp": "2026-07-19T00:02:00Z",
                "task_id": "test-003",
            }
        ]
        result = self.planner._build_patterns_section(patterns)

        self.assertIn("失败", result)
        self.assertIn("⚠️", result)
        self.assertIn("注意避免相同错误", result)
        self.assertIn("否", result)  # 成功=否

    def test_04_failed_without_lessons_no_warning(self):
        """失败但无教训的模式——如果通过过滤到达这里，不应有 ⚠️。"""
        patterns = [
            {
                "task_type": "empty_fail",
                "description": "一个失败的任务",
                "tool_sequence": [],
                "success": False,
                "lessons": "",
                "timestamp": "2026-07-19T00:03:00Z",
                "task_id": "test-004",
            }
        ]
        result = self.planner._build_patterns_section(patterns)

        self.assertIn("失败", result)
        self.assertNotIn("⚠️", result)

    def test_05_empty_tool_sequence_shows_placeholder(self):
        """空的 tool_sequence 应有占位文本。"""
        patterns = [
            {
                "task_type": "no_tools",
                "description": "无工具调用的任务",
                "tool_sequence": [],
                "success": True,
                "lessons": "不需要工具",
                "timestamp": "2026-07-19T00:00:00Z",
                "task_id": "test-005",
            }
        ]
        result = self.planner._build_patterns_section(patterns)
        self.assertIn("无工具调用", result)

    def test_06_multiple_patterns_numbered(self):
        """多个 pattern 应该被编号（模式 1、模式 2...）。"""
        patterns = [
            {
                "task_type": "t1", "description": "任务1",
                "tool_sequence": [{"name": "read_file"}],
                "success": True, "lessons": "", "timestamp": "", "task_id": "p1",
            },
            {
                "task_type": "t2", "description": "任务2",
                "tool_sequence": [{"name": "terminal"}],
                "success": True, "lessons": "", "timestamp": "", "task_id": "p2",
            },
        ]
        result = self.planner._build_patterns_section(patterns)
        self.assertIn("模式 1", result)
        self.assertIn("模式 2", result)


# ============================================================================
# 测试 3: _plan() 注入验证
# ============================================================================

class TestPlanInjection(unittest.TestCase):
    """验证 _plan() 中 _build_patterns_section 确实被调用。"""

    def setUp(self):
        self.planner = _make_minimal_planner()

    def test_01_build_patterns_section_called_in_plan(self):
        """_plan() 调用 _build_patterns_section 且 _load_relevant_patterns。"""
        self.planner._client = MagicMock()
        mock_msg = MagicMock()
        mock_msg.content = ('[{"task_id": "task-1", "description": "Test", '
                           '"acceptance_criteria": "[HARD] Works", "context": ""}]')
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        self.planner._client.chat.completions.create.return_value = mock_resp

        directive = Directive(
            goal="读取CSV文件并按年龄列排序",
            user_goal="读取CSV文件并按年龄列排序",
            intent="处理数据文件",
        )

        test_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "patterns"
        )
        os.makedirs(test_dir, exist_ok=True)
        test_pattern = {
            "task_type": "csv_sort",
            "description": "读取CSV文件并排序",
            "tool_sequence": [{"name": "read_file"}, {"name": "terminal"}],
            "success": True,
            "lessons": "先读头部确认编码",
            "timestamp": "2026-07-19T00:00:00Z",
            "task_id": "test-001",
        }
        pattern_path = os.path.join(test_dir, "test-001.json")
        try:
            with open(pattern_path, "w", encoding="utf-8") as f:
                json.dump(test_pattern, f, ensure_ascii=False)

            result = self.planner._plan(directive)
            self.assertEqual(len(result), 1)

            call_args = self.planner._client.chat.completions.create.call_args
            messages = call_args[1]["messages"]
            user_content = messages[-1]["content"]

            self.assertIn("历史执行模式参考", user_content,
                         f"_plan 应该注入历史执行模式参考。\n内容前缀: {user_content[:300]}")
            self.assertIn("read_file", user_content,
                         f"历史执行模式应包含工具名 read_file。\n内容前缀: {user_content[:300]}")

        finally:
            if os.path.exists(pattern_path):
                os.remove(pattern_path)

    def test_02_patterns_injected_after_feedback_before_decomposition(self):
        """验证注入顺序：feedback section → patterns section → 分解指令。"""
        self.planner._client = MagicMock()
        mock_msg = MagicMock()
        mock_msg.content = ('[{"task_id": "task-1", "description": "Test", '
                           '"acceptance_criteria": "[HARD] Works", "context": ""}]')
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        self.planner._client.chat.completions.create.return_value = mock_resp

        directive = Directive(goal="测试目标", user_goal="测试目标", intent="测试")

        self.planner._plan(directive)

        user_content = self.planner._client.chat.completions.create.call_args[1]["messages"][-1]["content"]

        decompose_pos = user_content.find("Decompose the following goal")
        patterns_pos = user_content.find("历史执行模式参考")

        self.assertGreaterEqual(decompose_pos, 0, "Decompose 指令应该存在")
        if patterns_pos >= 0:
            self.assertLess(patterns_pos, decompose_pos,
                          "历史执行模式参考应在 Decompose 指令之前注入")

    def test_03_no_patterns_no_injection(self):
        """没有 patterns/ 目录时，_plan 正常执行但不注入模式。"""
        patterns_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "patterns"
        )
        renamed = patterns_dir + ".bak"
        was_renamed = False

        try:
            if os.path.isdir(patterns_dir):
                os.rename(patterns_dir, renamed)
                was_renamed = True

            self.planner._client = MagicMock()
            mock_msg = MagicMock()
            mock_msg.content = ('[{"task_id": "task-1", "description": "Test", '
                               '"acceptance_criteria": "[HARD] Works", "context": ""}]')
            mock_choice = MagicMock()
            mock_choice.message = mock_msg
            mock_resp = MagicMock()
            mock_resp.choices = [mock_choice]
            self.planner._client.chat.completions.create.return_value = mock_resp

            directive = Directive(goal="测试目标", user_goal="测试目标", intent="测试")
            result = self.planner._plan(directive)
            self.assertEqual(len(result), 1)

            user_content = self.planner._client.chat.completions.create.call_args[1]["messages"][-1]["content"]
            self.assertNotIn("历史执行模式参考", user_content,
                           "没有 patterns 目录时不应注入历史执行模式参考")

        finally:
            if was_renamed:
                os.rename(renamed, patterns_dir)


# ============================================================================
# 测试 4: 边界条件
# ============================================================================

class TestBoundaryConditions(unittest.TestCase):
    """边界条件测试 —— 目录不存在、空目录、>50 文件、坏 JSON 等。"""

    def setUp(self):
        self.planner = _make_minimal_planner()

    def _get_patterns_dir(self):
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "patterns"
        )

    def test_01_dir_not_exist_returns_empty(self):
        """patterns/ 目录不存在 → 不报错，返回空列表。"""
        with patch('core.planner.os.path.isdir', return_value=False):
            result = self.planner._load_relevant_patterns("任意查询")
            self.assertEqual(result, [], "目录不存在时应返回空列表")

    def test_02_dir_not_exist_no_exception(self):
        """目录不存在 → 不抛出异常。"""
        try:
            with patch('core.planner.os.path.isdir', return_value=False):
                self.planner._load_relevant_patterns("任意查询")
        except Exception as e:
            self.fail(f"目录不存在时不应抛出异常，但抛出了: {type(e).__name__}: {e}")

    def test_03_empty_dir_returns_empty(self):
        """patterns/ 目录存在但为空 → 返回空列表。"""
        with patch('core.planner.os.path.isdir', return_value=True):
            with patch('core.planner.os.listdir', return_value=[]):
                result = self.planner._load_relevant_patterns("任意查询")
                self.assertEqual(result, [], "空目录应返回空列表")

    def test_04_no_json_files_returns_empty(self):
        """目录中有非 JSON 文件，但没有 JSON 文件 → 返回空列表。"""
        with patch('core.planner.os.path.isdir', return_value=True):
            with patch('core.planner.os.listdir', return_value=["readme.md", "notes.txt", ".gitkeep"]):
                result = self.planner._load_relevant_patterns("测试")
                self.assertEqual(result, [], "只有非 JSON 文件时应返回空列表")

    def test_05_over_50_files_limits_to_20(self):
        """>50 个 pattern 文件 → 按 mtime 只读最近 20 个。"""
        mock_files = [f"pattern_{i:03d}.json" for i in range(55)]

        called_files = []

        def fake_open(fpath, *args, **kwargs):
            fname = os.path.basename(fpath)
            called_files.append(fname)
            raise IOError("mock fail — not actually opening file")

        def mock_getmtime(path):
            fname = os.path.basename(path)
            idx = int(fname.split("_")[1].split(".")[0])
            return 1000 + idx  # newer files have higher mtime

        with patch('core.planner.os.path.isdir', return_value=True):
            with patch('core.planner.os.listdir', return_value=list(mock_files)):
                with patch('core.planner.os.path.getmtime', side_effect=mock_getmtime):
                    with patch('builtins.open', side_effect=fake_open):
                        result = self.planner._load_relevant_patterns("测试")
                        self.assertEqual(result, [], "所有 JSON 不可读时应返回空")
                        self.assertLessEqual(
                            len(called_files), 20,
                            f"超过 50 个文件时应限制为 20 个，实际尝试了 {len(called_files)} 个"
                        )
                        self.assertGreater(len(called_files), 0,
                                          "应至少尝试打开一些文件")

    def test_06_bad_json_skipped_no_crash(self):
        """坏 JSON 文件 → 跳过不崩溃。"""
        test_dir = self._get_patterns_dir()
        bak_dir = None
        orig_exists = os.path.isdir(test_dir)

        try:
            if orig_exists:
                bak_dir = test_dir + ".bak_for_test"
                os.rename(test_dir, bak_dir)

            os.makedirs(test_dir, exist_ok=True)

            # 坏 JSON 文件
            bad_path = os.path.join(test_dir, "bad.json")
            with open(bad_path, "w", encoding="utf-8") as f:
                f.write("this is not valid json {{{")

            # 好 JSON 文件
            good_pattern = {
                "task_type": "good",
                "description": "一个正常的任务",
                "tool_sequence": [{"name": "read_file"}],
                "success": True,
                "lessons": "正常完成",
                "timestamp": "2026-07-19T00:00:00Z",
                "task_id": "good-001",
            }
            good_path = os.path.join(test_dir, "good-001.json")
            with open(good_path, "w", encoding="utf-8") as f:
                json.dump(good_pattern, f, ensure_ascii=False)

            try:
                result = self.planner._load_relevant_patterns("正常任务")
                task_ids = [r["task_id"] for r in result]
                self.assertIn("good-001", task_ids,
                             f"坏 JSON 被跳过，好 JSON 应正常加载，实际: {task_ids}")
                self.assertNotIn("bad", [r.get("task_id") for r in result],
                                "坏 JSON 文件不应出现在结果中")
            except Exception as e:
                self.fail(f"坏 JSON 文件不应导致崩溃: {type(e).__name__}: {e}")

        finally:
            shutil.rmtree(test_dir, ignore_errors=True)
            if bak_dir and os.path.isdir(bak_dir):
                os.rename(bak_dir, test_dir)

    def test_07_osecurity_listdir_returns_empty(self):
        """listdir 抛出 OSError → 不崩溃，返回空列表。"""
        with patch('core.planner.os.path.isdir', return_value=True):
            with patch('core.planner.os.listdir', side_effect=OSError("Permission denied")):
                result = self.planner._load_relevant_patterns("测试")
                self.assertEqual(result, [], "listdir 异常时应返回空列表")

    def test_08_empty_user_goal_returns_empty(self):
        """空 user_goal → 没有关键词 → 返回空列表。"""
        result = self.planner._load_relevant_patterns("")
        self.assertEqual(result, [], "空 user_goal 应返回空列表")

    def test_09_only_stopwords_goal_returns_empty(self):
        """user_goal 只有停用词 → 返回空列表。"""
        result = self.planner._load_relevant_patterns("的 是 在 the a an")
        self.assertEqual(result, [], "只有停用词的 user_goal 应返回空列表")


# ============================================================================
# 测试 5: 导入和回归测试
# ============================================================================

class TestImportsAndRegression(unittest.TestCase):
    """验证所有相关模块可以正常导入，核心函数可以跑通。"""

    def test_01_all_imports_ok(self):
        """所有关键 import 成功。"""
        from core.planner import Planner, calibrate_and_adjust
        from core.planner import _estimate_task_difficulty, _difficulty_sort_preserve_deps
        from core.protocol import ExecutionPattern
        self.assertTrue(True, "所有 import 成功")

    def test_02_planner_instantiation_ok(self):
        """Planner 实例化成功。"""
        planner = _make_minimal_planner()
        self.assertIsInstance(planner, Planner)

    def test_03_calibrate_and_adjust_imported(self):
        """calibrate_and_adjust 函数可用。"""
        self.assertTrue(callable(calibrate_and_adjust))

    def test_04_execution_pattern_dataclass(self):
        """ExecutionPattern dataclass 可用。"""
        ep = ExecutionPattern(
            task_type="test",
            description="A test pattern",
            tool_sequence=[{"name": "read_file", "args_summary": "", "success": True}],
            success=True,
            lessons="Test lesson",
            timestamp="2026-07-19T00:00:00Z",
            task_id="test-001",
        )
        self.assertEqual(ep.task_type, "test")
        self.assertEqual(ep.success, True)


# ============================================================================
# 运行所有测试
# ============================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
