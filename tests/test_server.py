"""Drive the RPC server with in-memory streams and assert on the frames."""

from __future__ import annotations

import io
import json
import unittest

from engine.methods import register_all
from engine.rpc import protocol
from engine.rpc.server import RpcServer


def run_frames(raw_frames: list[str]) -> list[dict]:
    stdin = io.StringIO("\n".join(raw_frames) + "\n")
    stdout = io.StringIO()
    server = RpcServer(stdin=stdin, stdout=stdout)
    register_all(server)
    server.serve()
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]


def request(request_id: object, method: str, params: dict | None = None) -> str:
    return protocol.dumps(protocol.make_request(request_id, method, params if params is not None else {}))


class EnvelopeTests(unittest.TestCase):
    def test_ping_returns_result_with_matching_id(self) -> None:
        frames = run_frames([request(1, "system.ping")])
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["id"], 1)
        self.assertTrue(frames[0]["result"]["pong"])
        self.assertIn("engine_version", frames[0]["result"])

    def test_ids_are_echoed_verbatim(self) -> None:
        frames = run_frames([request("abc-1", "system.ping"), request(99, "system.ping")])
        self.assertEqual([frame["id"] for frame in frames], ["abc-1", 99])

    def test_blank_lines_are_ignored(self) -> None:
        frames = run_frames(["", "   ", request(1, "system.ping"), ""])
        self.assertEqual(len(frames), 1)

    def test_notification_produces_no_response(self) -> None:
        notification = protocol.dumps(protocol.make_notification("system.ping", {}))
        frames = run_frames([notification])
        self.assertEqual(frames, [])


class ErrorTests(unittest.TestCase):
    def assert_error(self, frame: dict, expected_code: str) -> None:
        self.assertIn("error", frame, f"expected an error, got {frame}")
        self.assertEqual(frame["error"]["data"]["code"], expected_code)

    def test_malformed_json_yields_parse_error(self) -> None:
        frames = run_frames(["{not json"])
        self.assert_error(frames[0], "PARSE_ERROR")
        self.assertIsNone(frames[0]["id"])

    def test_unknown_method_yields_method_not_found(self) -> None:
        frames = run_frames([request(1, "does.not.exist")])
        self.assert_error(frames[0], "METHOD_NOT_FOUND")

    def test_unexpected_property_is_rejected(self) -> None:
        frames = run_frames([request(1, "system.ping", {"bogus": True})])
        self.assert_error(frames[0], "INVALID_PARAMS")

    def test_missing_required_param_is_rejected(self) -> None:
        frames = run_frames([request(1, "media.probe", {})])
        self.assert_error(frames[0], "INVALID_PARAMS")

    def test_a_method_marked_implemented_needs_a_handler(self) -> None:
        # Every declared method now has a handler, so the NOT_IMPLEMENTED reply is
        # no longer reachable through the contract. What still guards the surface
        # is this: a contract that claims a method is implemented while nothing
        # registered a handler must refuse to start rather than serve a stub.
        from engine.methods import stubs

        server = RpcServer(stdin=io.StringIO(), stdout=io.StringIO())
        with self.assertRaises(RuntimeError):
            stubs.register_stubs(server)

    def test_wrong_jsonrpc_version_is_rejected(self) -> None:
        frames = run_frames(['{"jsonrpc":"1.0","id":1,"method":"system.ping","params":{}}'])
        self.assert_error(frames[0], "INVALID_REQUEST")

    def test_error_carries_problem_list(self) -> None:
        frames = run_frames([request(1, "system.ping", {"bogus": 1})])
        problems = frames[0]["error"]["data"]["detail"]["problems"]
        self.assertTrue(any("bogus" in item for item in problems))


class LifecycleTests(unittest.TestCase):
    def test_shutdown_replies_then_stops_reading(self) -> None:
        frames = run_frames([request(1, "system.shutdown"), request(2, "system.ping")])
        self.assertEqual(len(frames), 1, "frames after shutdown must not be processed")
        self.assertTrue(frames[0]["result"]["ok"])

    def test_registry_covers_the_whole_contract(self) -> None:
        from engine.generated.contract import IMPLEMENTED_METHODS, METHODS

        stdin = io.StringIO("")
        server = RpcServer(stdin=stdin, stdout=io.StringIO())
        register_all(server)

        self.assertEqual(set(server.registered()), set(METHODS))
        for name in IMPLEMENTED_METHODS:
            self.assertIn(name, server.registered())

    def test_registering_unknown_method_is_a_wiring_error(self) -> None:
        server = RpcServer(stdin=io.StringIO(""), stdout=io.StringIO())
        with self.assertRaises(ValueError):
            server.register("not.in.contract", lambda _params: {})

    def test_double_registration_is_rejected(self) -> None:
        server = RpcServer(stdin=io.StringIO(""), stdout=io.StringIO())
        server.register("system.ping", lambda _params: {})
        with self.assertRaises(ValueError):
            server.register("system.ping", lambda _params: {})


class StreamPurityTests(unittest.TestCase):
    """stdout must stay parseable no matter what the engine logs."""

    def test_stderr_noise_does_not_corrupt_stdout(self) -> None:
        stdin = io.StringIO(request(1, "system.ping") + "\n" + request(2, "system.ping") + "\n")
        stdout = io.StringIO()
        stderr = io.StringIO()

        import contextlib

        server = RpcServer(stdin=stdin, stdout=stdout)
        register_all(server)
        with contextlib.redirect_stderr(stderr):
            print("deliberate noise\n" * 500, file=stderr)
            server.serve()

        frames = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
        self.assertEqual([frame["id"] for frame in frames], [1, 2])

    def test_every_outbound_line_is_a_single_valid_json_object(self) -> None:
        frames = run_frames([request(1, "system.capabilities")])
        self.assertEqual(len(frames), 1)
        self.assertIsInstance(frames[0]["result"]["methods"], list)


if __name__ == "__main__":
    unittest.main()
