"""Offline wire contracts for optional output budgets and provider switching."""

import json
import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent_loop import AgentConfig, AgentLoop
from src.cli import apply_provider_profile, build_provider_profiles, create_agent, write_config_template
from src.provider import AnthropicProvider, OpenAIProvider, ProviderConfig, ProviderProfileConfig
from src.model_registry import merge_active_provider
from src.desktop_service import DEFAULT_CONFIG, DesktopService
from src.streaming import EventType, StreamEvent


class OptionalOutputBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.resources = []
        self.requests = []
        asyncio.get_running_loop().slow_callback_duration = 20

    async def asyncTearDown(self):
        for resource in reversed(self.resources):
            await resource.close()
        self.temp.cleanup()

    def config(self, **kwargs):
        return ProviderConfig(base_url="https://offline.invalid", model="offline", **kwargs)

    async def capture(self, owner):
        self.resources.append(owner)
        await owner.client.aclose()

        def handle(request):
            self.requests.append(json.loads(request.content))
            return httpx.Response(200, content=b"data: [DONE]\n\n")

        owner.client = httpx.AsyncClient(base_url="https://offline.invalid",
                                        transport=httpx.MockTransport(handle))
        return owner

    async def send_agent(self, agent):
        response = await agent._send_stream_request(agent.get_request_path(), agent.build_request_body("system"))
        await response.aclose()
        return self.requests[-1]

    async def test_openai_wire_omits_unset_and_zero_but_preserves_positive(self):
        for kwargs, expected in (({}, None), ({"max_tokens": None}, None),
                                 ({"max_tokens": 0}, None), ({"max_tokens": "0"}, None),
                                 ({"max_tokens": 4096}, 4096), ({"max_tokens": "4096"}, 4096)):
            for entry in ("provider", "agent", "cli"):
                with self.subTest(kwargs=kwargs, entry=entry):
                    if entry == "provider":
                        owner = await self.capture(OpenAIProvider(self.config(**kwargs)))
                        response = await owner.stream_create([{"role": "user", "content": "test"}], "system")
                        await response.aclose()
                        body = self.requests[-1]
                    else:
                        if entry == "agent":
                            owner = AgentLoop(AgentConfig(provider=self.config(**kwargs), cwd=self.temp.name))
                        else:
                            owner = create_agent({"base_url": "https://offline.invalid", "model": "offline", **kwargs},
                                                 "system", cwd=self.temp.name)
                        body = await self.send_agent(await self.capture(owner))
                    if expected is None:
                        self.assertNotIn("max_tokens", body)
                        self.assertNotIn("max_completion_tokens", body)
                    else:
                        self.assertEqual(body["max_tokens"], expected)

    async def test_anthropic_wire_preserves_explicit_budget(self):
        for entry in ("provider", "agent", "cli"):
            with self.subTest(entry=entry):
                config = self.config(format="anthropic", max_tokens=8192, thinking_budget=1024)
                if entry == "provider":
                    owner = await self.capture(AnthropicProvider(config))
                    response = await owner.stream_create([{"role": "user", "content": "test"}], "system")
                    await response.aclose()
                    body = self.requests[-1]
                else:
                    owner = (AgentLoop(AgentConfig(provider=config, cwd=self.temp.name)) if entry == "agent"
                             else create_agent({"base_url": config.base_url, "provider": "anthropic",
                                                "max_tokens": 8192, "thinking_budget": 1024}, "system", cwd=self.temp.name))
                    body = await self.send_agent(await self.capture(owner))
                self.assertEqual(body["max_tokens"], 8192)
                self.assertEqual(body["thinking"]["budget_tokens"], 1024)

    async def test_anthropic_requires_budget_before_any_http_request(self):
        for kwargs in ({}, {"max_tokens": None}, {"max_tokens": 0}):
            for entry in ("provider", "agent"):
                with self.subTest(kwargs=kwargs, entry=entry):
                    config = self.config(format="anthropic", **kwargs)
                    owner = (AnthropicProvider(config) if entry == "provider"
                             else AgentLoop(AgentConfig(provider=config, cwd=self.temp.name)))
                    await self.capture(owner)
                    with self.assertRaisesRegex(ValueError, "Anthropic requires an explicit positive max_tokens"):
                        if entry == "provider":
                            await owner.stream_create([], "")
                        else:
                            await self.send_agent(owner)
        self.assertEqual(self.requests, [])

    async def test_explicit_provider_class_uses_its_own_protocol(self):
        owner = await self.capture(AnthropicProvider(self.config()))
        with self.assertRaisesRegex(ValueError, "Anthropic requires"):
            await owner.stream_create([], "")
        self.assertEqual(self.requests, [])
        owner = await self.capture(OpenAIProvider(self.config(format="anthropic")))
        response = await owner.stream_create([], "")
        await response.aclose()
        self.assertNotIn("max_tokens", self.requests[-1])

    async def test_profile_switch_does_not_restore_a_hidden_budget(self):
        for override in (None, 0, 4096):
            config = {"active_provider": "active", "max_tokens": override, "providers": {
                "active": {"base_url": "https://offline.invalid", "max_tokens": 12000},
                "unset": {"base_url": "https://offline.invalid"},
                "null": {"base_url": "https://offline.invalid", "max_tokens": None},
                "zero": {"base_url": "https://offline.invalid", "max_tokens": 0},
                "limited": {"base_url": "https://offline.invalid", "max_tokens": 2048},
            }}
            profiles = build_provider_profiles(config)
            expected = [override or None, None, None, None, 2048]
            self.assertEqual([p.max_tokens for p in profiles], expected)
            owner = AgentLoop(AgentConfig(provider=self.config(max_tokens=12000), cwd=self.temp.name))
            self.resources.append(owner)
            for profile, budget in zip(profiles, expected):
                apply_provider_profile(owner, profile, "offline")
                body = owner.build_request_body("")
                if budget is None:
                    self.assertNotIn("max_tokens", body)
                else:
                    self.assertEqual(body["max_tokens"], budget)

    def test_invalid_budgets_are_rejected_without_silent_coercion(self):
        for value in (-1, "-1", 1.5, True, "invalid", "1.5"):
            for config_type in (ProviderConfig, ProviderProfileConfig):
                with self.subTest(value=value, config_type=config_type.__name__):
                    with self.assertRaisesRegex(ValueError, "max_tokens"):
                        config_type(max_tokens=value)
            with self.assertRaisesRegex(ValueError, "max_tokens"):
                build_provider_profiles({"max_tokens": value})

    def test_starter_template_and_default_profile_leave_budget_unset(self):
        path = str(Path(self.temp.name) / "config.json")
        write_config_template(path)
        config = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertIsNone(config["max_tokens"])
        self.assertIsNone(build_provider_profiles(config)[0].max_tokens)

    def test_active_profile_null_overrides_legacy_global_budget(self):
        for value in (None, 0, 1024):
            merged = merge_active_provider({"max_tokens": 100000, "active_provider": "selected",
                                            "providers": {"selected": {"max_tokens": value}}})
            self.assertEqual(merged["max_tokens"], value)
            self.assertEqual(build_provider_profiles(merged)[0].max_tokens, value or None)

    def test_desktop_budget_validation_matches_provider_contract(self):
        self.assertIsNone(DEFAULT_CONFIG["max_tokens"])
        for value in (None, 0, 2048):
            config = {**DEFAULT_CONFIG, "max_tokens": value}
            DesktopService._validate_config(config)
            self.assertEqual(config["max_tokens"], value or None)
        for value in (None, 0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                DesktopService._validate_config({**DEFAULT_CONFIG, "provider": "anthropic", "max_tokens": value})
        config = {**DEFAULT_CONFIG, "provider": "anthropic", "max_tokens": 8192}
        DesktopService._validate_config(config)
        self.assertEqual(config["max_tokens"], 8192)

    async def test_none_run_budgets_pass_old_turn_limit_and_keep_failure_guard(self):
        owner = create_agent({"max_run_turns": None, "max_run_seconds": None, "max_auto_continues": None},
                             "", cwd=self.temp.name, event_sink=lambda event: None)
        self.resources.append(owner)
        self.assertIsNone(owner.config.max_auto_continues)
        owner._run_one_turn = AsyncMock(side_effect=[True] * 45 + [False])
        await owner.run("continue")
        self.assertEqual(owner._run_one_turn.await_count, 46)
        self.assertEqual(owner.stopped_reason, "completed")

        async def failed():
            owner._last_turn_failed = True
            return True

        owner._run_one_turn = AsyncMock(side_effect=failed)
        await owner.run("fail")
        self.assertEqual(owner._run_one_turn.await_count, 3)
        self.assertEqual(owner.stopped_reason, "max_consecutive_failures")

    async def test_none_continuation_budget_passes_old_limit_and_zero_still_disables(self):
        for budget, expected_turns, reason in ((None, 11, "completed"), (0, 1, "max_auto_continues")):
            owner = AgentLoop(AgentConfig(provider=self.config(), cwd=self.temp.name,
                                         max_auto_continues=budget, max_run_turns=None, max_run_seconds=None),
                              event_sink=lambda event: None)
            self.resources.append(owner)
            response = AsyncMock()
            owner._send_stream_request = AsyncMock(return_value=response)
            count = 0

            async def stream(_response):
                nonlocal count
                count += 1
                yield StreamEvent(type=EventType.TEXT_DELTA, text="continue")
                yield StreamEvent(type=EventType.MESSAGE_STOP, finish_reason="length" if count <= 10 else "stop")

            owner._process_stream = stream
            await owner.run("test")
            self.assertEqual(owner._send_stream_request.await_count, expected_turns)
            self.assertEqual(owner.stopped_reason, reason)

    async def test_explicit_finite_run_limits_remain_active(self):
        for limits, reason in (({"max_run_turns": 2, "max_run_seconds": None}, "max_run_turns"),
                               ({"max_run_turns": None, "max_run_seconds": 0.01}, "max_run_seconds")):
            owner = AgentLoop(AgentConfig(cwd=self.temp.name, **limits), event_sink=lambda event: None)
            self.resources.append(owner)

            async def turn():
                await asyncio.sleep(0.02)
                return True

            owner._run_one_turn = AsyncMock(side_effect=turn)
            await owner.run("test")
            self.assertEqual(owner.stopped_reason, reason)

    def test_run_budget_validation_rejects_negative_and_nonfinite_values(self):
        for name in ("max_run_turns", "max_auto_continues", "max_run_seconds"):
            for value in (-1, True, float("inf"), float("nan")):
                with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, name):
                    AgentConfig(**{name: value})


if __name__ == "__main__":
    unittest.main()
