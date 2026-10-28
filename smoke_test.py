"""Exercise the real game bridge without calling any AI provider or changing rules."""
import argparse
import json
from pathlib import Path

from engine import GameProcess, Session, load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path)
    parser.add_argument("--headless", action="store_true", help="Only for automated checks; normal launcher is always visible")
    parser.add_argument("--steps", type=int, default=35)
    args = parser.parse_args()
    config = load_config()
    if args.game:
        config["game_path"] = str(args.game.resolve())
    config["seed"] = 7
    session = Session(config)
    game = GameProcess(config, session.directory)
    try:
        bridge = game.launch(headless=args.headless)
        menu = bridge.observe()
        assert menu["screen"] == "menu"
        state = bridge.act(menu, {"kind": "start_run", "scenario": "arcadia"})
        observed = set()
        plays = 0
        for step in range(args.steps):
            assert "deck" not in state and "seed" not in state and "rng" not in state
            actions = state["legal_actions"]
            if state.get("run_status") in (1, 2):
                break
            assert actions, state
            kind = state.get("decision")
            observed.add(kind)
            # Simple transport probe, deliberately not a balance/player-quality test.
            if kind == "main":
                cards = [a for a in actions if a["kind"] == "play_card"]
                action = cards[0] if cards and plays < 2 else {"kind": "end_turn"}
                if action["kind"] == "play_card":
                    plays += 1
                else:
                    plays = 0
            else:
                action = actions[0]
            before = state
            state = bridge.act(state, action)
            session.record(before, {"action": action, "reason": "非AI接口检查", "feedback": ""}, state, "smoke_test")
            assert not state.get("flight_active"), "act must wait until the visual flight completes"
        # Rejected stale requests cannot silently apply an earlier model decision.
        if state["legal_actions"]:
            result = bridge.call("observe")
            assert result["observation"]["revision"] == state["revision"]
            try:
                bridge.call("act", revision=-1, action=state["legal_actions"][0])
                raise AssertionError("Stale action was accepted")
            except RuntimeError as error:
                assert "stale_observation" in str(error)
            try:
                bridge.call("act", revision=state["revision"], action={"kind": "set_budget", "amount": 999})
                raise AssertionError("Arbitrary mutation was accepted")
            except RuntimeError as error:
                assert "illegal_action" in str(error)
        if not args.headless:
            screenshot = bridge.screenshot()
            assert screenshot.startswith(b"\x89PNG")
            session.capture(screenshot, 9999)
        print(json.dumps({"result": "PASS", "steps": len(session.decisions), "turn": state.get("turn"),
                          "decision_types": sorted(observed), "visible_window": not args.headless,
                          "model_requests": 0, "run_directory": str(session.directory)}, ensure_ascii=False))
    finally:
        game.close()


if __name__ == "__main__":
    main()
