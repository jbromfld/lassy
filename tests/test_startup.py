"""Startup errors identify safe recovery actions without exposing raw exception text."""

import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch

from lassy.api import create_app
from lassy.errors import DEFAULT_STARTUP_MESSAGE, StartupError, startup_message
from lassy.service import check_schema


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_identity_table_identifies_exact_migration(self):
        conn = Mock(fetchval=AsyncMock(side_effect=["embeddings", "documents", None]))
        with self.assertRaises(StartupError) as failure:
            await check_schema(conn, 768)
        self.assertEqual(failure.exception.code, "missing_identity_schema")
        self.assertIn("db/004_mcp.sql", startup_message(failure.exception))
        self.assertIn("adopt", startup_message(failure.exception))

    async def test_api_lifespan_logs_actionable_safe_error(self):
        @asynccontextmanager
        async def unavailable():
            raise StartupError("missing_identity_schema")
            yield

        with patch("lassy.api.open_service", unavailable):
            app = create_app()
            with self.assertLogs("lassy.api", level="CRITICAL") as logs:
                with self.assertRaisesRegex(RuntimeError, "db/004_mcp.sql"):
                    async with app.router.lifespan_context(app):
                        self.fail("startup should fail")
        self.assertIn("lassy_index_identity", " ".join(logs.output))

    async def test_unknown_startup_error_never_logs_raw_details(self):
        @asynccontextmanager
        async def unavailable():
            raise RuntimeError("PRIVATE_PASSWORD_AND_DOCUMENT")
            yield

        with patch("lassy.api.open_service", unavailable):
            app = create_app()
            with self.assertLogs("lassy.api", level="CRITICAL") as logs:
                with self.assertRaises(RuntimeError) as failure:
                    async with app.router.lifespan_context(app):
                        self.fail("startup should fail")
        self.assertNotIn("PRIVATE_", str(failure.exception) + " ".join(logs.output))
        self.assertIn(DEFAULT_STARTUP_MESSAGE, str(failure.exception))

    def test_mcp_and_model_cli_keep_known_recovery_errors_safe(self):
        from lassy import mcp, models

        with patch.object(
            mcp,
            "build_server",
            Mock(
                side_effect=ExceptionGroup(
                    "private wrapper", [StartupError("identity_unverified")]
                )
            ),
        ):
            with self.assertRaisesRegex(SystemExit, "adopt-index"):
                mcp.main()
        with (
            patch("sys.argv", ["lassy-models", "adopt-index"]),
            patch(
                "lassy.service.adopt_index",
                AsyncMock(side_effect=StartupError("missing_identity_schema")),
            ),
            patch("lassy.config.load_settings", Mock()),
        ):
            with self.assertRaisesRegex(SystemExit, "004_mcp.sql"):
                models.main()
        self.assertEqual(
            startup_message(
                ExceptionGroup("PRIVATE_PASSWORD", [ValueError("PRIVATE_SOURCE")])
            ),
            DEFAULT_STARTUP_MESSAGE,
        )
