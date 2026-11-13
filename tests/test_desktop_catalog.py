"""The desktop catalog reflects the real runtime without executing tools."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent_loop import AgentConfig, AgentLoop
from src.desktop_catalog import read_catalog_skill, runtime_catalog
from src.interfaces import CustomToolConfig, InterfaceConfig
from src.provider import ProviderConfig


class DesktopCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory(prefix="thinkflow-desktop-catalog-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        directory = root / "catalog-fixture"
        directory.mkdir()
        self.skill_path = directory / "SKILL.md"
        self.skill_path.write_text(
            "---\nname: catalog-fixture\ndescription: 本地目录测试\n---\n\n唯一的技能正文。\n",
            encoding="utf-8",
        )
        self.agent = AgentLoop(AgentConfig(
            cwd=str(root),
            provider=ProviderConfig(base_url="https://fixture.invalid", enable_native_tools=True),
            interfaces=InterfaceConfig(custom_tools=[
                CustomToolConfig("catalog_probe", "配置扩展", {}, ["fixture-command"]),
                CustomToolConfig("read", "同名扩展", {}, ["fixture-command"]),
                CustomToolConfig("not_registered", "停用扩展", {}, ["fixture-command"], enabled=False),
            ]),
        ), event_sink=lambda _: None)
        self.addAsyncCleanup(self.agent.close)
        roots = patch.object(self.agent.skill_manager, "_candidate_roots", return_value=[(str(root), "config")])
        roots.start()
        self.addCleanup(roots.stop)

    def tools(self):
        return {tool["name"]: tool for tool in runtime_catalog(self.agent)["tools"]}

    async def test_catalog_reports_actual_native_and_text_entry_points(self):
        tools = self.tools()
        self.assertTrue(tools["read"]["native_enabled"])
        self.assertTrue(tools["read"]["text_protocol"])
        self.assertTrue(tools["grep"]["on_demand"])
        self.assertTrue(tools["grep"]["text_protocol"])
        self.assertTrue(tools["update_plan"]["text_protocol"])
        self.assertEqual(tools["update_plan"]["flow"], 'delayed')

        self.agent.additional_native_tools.add("grep")
        self.assertTrue(self.tools()["grep"]["native_enabled"])
        self.assertFalse(self.tools()["grep"]["on_demand"])

        provider = self.agent.config.provider
        provider.native_tools = ["read", "grep"]
        provider.disabled_native_tools = ["read"]
        tools = self.tools()
        self.assertTrue(tools["grep"]["native_enabled"])
        self.assertFalse(tools["read"]["native_enabled"])
        self.assertTrue(tools["read"]["text_protocol"])
        self.assertFalse(any(tool["on_demand"] for tool in tools.values()))

        provider.enable_native_tools = False
        tools = self.tools()
        self.assertTrue(tools["read"]["text_protocol"])
        self.assertFalse(any(tool["native_enabled"] or tool["on_demand"] for tool in tools.values()))

    async def test_catalog_uses_registered_extensions_and_reads_skill_locally(self):
        with patch.object(self.agent.tool_registry, "execute", side_effect=AssertionError("Tool executed")):
            catalog = runtime_catalog(self.agent)
            result = read_catalog_skill(self.agent, "catalog-fixture")
        tools = {tool["name"]: tool for tool in catalog["tools"]}
        self.assertNotIn("not_registered", tools)
        self.assertFalse(tools["read"]["extension"])
        self.assertTrue(tools["catalog_probe"]["extension"])
        self.assertEqual(tools["catalog_probe"]["flow"], "confirm")
        self.assertEqual(tools["catalog_probe"]["risk"], "high")
        self.assertEqual(catalog["skills"], [{
            "name": "catalog-fixture", "description": "本地目录测试",
            "path": str(self.skill_path), "source": "config",
        }])
        self.assertTrue(result["success"])
        self.assertIn("唯一的技能正文。", result["content"])
        self.assertFalse(read_catalog_skill(self.agent, "missing")["success"])
        json.dumps(catalog)


if __name__ == "__main__":
    unittest.main()
