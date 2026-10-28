extends SceneTree
## Optional external adapter. Run with --path GAME --script THIS_FILE.
## Only invokes the normal game UI and exposes player-visible information.

const VERSION := 1
const MAX_REQUEST := 65536
var _server := TCPServer.new()
var _peer: StreamPeerTCP
var _buffer := PackedByteArray()
var _token := ""
var _main: Control
var _handling := false
var _revision := 0
var _last_fingerprint := ""


func _initialize() -> void:
	call_deferred("_boot")


func _boot() -> void:
	_token = OS.get_environment("FINCARD_BRIDGE_TOKEN")
	var port := int(OS.get_environment("FINCARD_BRIDGE_PORT"))
	if _token.length() < 24 or port <= 0:
		push_error("Bridge requires a random token and local port from the launcher.")
		quit(1)
		return
	_main = load("res://presentation/main.tscn").instantiate()
	root.add_child(_main)
	_main.controller.seed_value = int(OS.get_environment("FINCARD_BRIDGE_SEED"))
	if _main.controller.seed_value != 0:
		_main.controller.simulation.rng = Rng.new(_main.controller.seed_value)
		_main.controller.simulation.state.seed_value = _main.controller.seed_value
	var error := _server.listen(port, "127.0.0.1")
	if error != OK:
		push_error("Cannot start the loopback-only bridge.")
		quit(1)
		return
	print("FINCARD_BRIDGE_READY v%d" % VERSION)


func _process(_delta: float) -> bool:
	if _server.is_connection_available():
		if _peer == null:
			_peer = _server.take_connection()
		else:
			_server.take_connection().disconnect_from_host()
	if _peer == null:
		return false
	_peer.poll()
	if _peer.get_status() != StreamPeerTCP.STATUS_CONNECTED:
		_peer = null
		_buffer.clear()
		return false
	var available := _peer.get_available_bytes()
	if available > 0:
		var received := _peer.get_data(available)
		if received[0] == OK:
			_buffer.append_array(received[1])
	if _buffer.size() > MAX_REQUEST:
		_peer.disconnect_from_host()
		return false
	if not _handling:
		var newline := _buffer.find(10)
		if newline >= 0:
			var line := _buffer.slice(0, newline).get_string_from_utf8()
			_buffer = _buffer.slice(newline + 1)
			_handling = true
			_handle(line)
	return false


func _handle(line: String) -> void:
	var request = JSON.parse_string(line)
	var response: Dictionary
	if not request is Dictionary or str(request.get("token", "")) != _token:
		response = {"ok": false, "error": "unauthorized"}
	else:
		match str(request.get("method", "")):
			"observe":
				response = {"ok": true, "observation": _observe()}
			"act":
				response = await _act(request)
			"screenshot":
				if DisplayServer.get_name() == "headless":
					response = {"ok": false, "error": "screenshots_require_visible_window"}
				else:
					await RenderingServer.frame_post_draw
					var png := root.get_texture().get_image().save_png_to_buffer()
					response = {"ok": true, "png_base64": Marshalls.raw_to_base64(png)}
			_:
				response = {"ok": false, "error": "unknown_method"}
	if _peer != null and _peer.get_status() == StreamPeerTCP.STATUS_CONNECTED:
		_peer.put_data((JSON.stringify(response) + "\n").to_utf8_buffer())
	_handling = false


func _ui() -> GameUI:
	return _main._screen as GameUI if is_instance_valid(_main) else null


func _card(id: StringName) -> Dictionary:
	var card: CardDef = _main.controller.cards.get(id)
	if card == null:
		return {}
	return {
		"id": String(id), "name": card.display_name, "type": String(card.card_type),
		"rarity": String(card.rarity), "description": card.description,
		"effects": TextFormatter.effects_text(_main.controller.labels, card.effects),
		"budget_cost": card.cost, "political_cost": card.political_cost,
		"once_per_run": card.once_per_run,
	}


func _reward(reward: Reward, group: Dictionary) -> Dictionary:
	var sim: Simulation = _main.controller.simulation
	var result := {"type": reward.type, "id": String(reward.id), "name": reward.label,
		"available": sim.reward_option_available(group, reward)}
	match reward.type:
		Reward.Type.CARD:
			result["card"] = _card(reward.id)
		Reward.Type.CABINET:
			var cabinet: CabinetDef = _main.controller.cabinets[reward.id]
			result["description"] = cabinet.description
			result["effects"] = TextFormatter.effects_text(_main.controller.labels, cabinet.modifiers)
			var triggers: Array = []
			for trigger in cabinet.triggers:
				triggers.append(TextFormatter.trigger_text(_main.controller.labels, trigger))
			result["triggers"] = triggers
		Reward.Type.AID:
			var aid: AidDef = _main.controller.aids[reward.id]
			result["effects"] = TextFormatter.effects_text(_main.controller.labels, aid.effects)
			result["gain_amount"] = sim.aid_gain_amount(reward.id)
	return result


func _fingerprint() -> String:
	var ui := _ui()
	if ui == null:
		return "menu:" + str(_main._screen.get_instance_id())
	var state: GameState = _main.controller.simulation.state
	var modifiers: Array = []
	for modifier in state.modifiers:
		modifiers.append([modifier.scope, modifier.target, modifier.op, modifier.value,
			modifier.remaining_turns, modifier.stacks, modifier.ready_turn, modifier.data])
	var groups: Array = []
	for group in state.pending_rewards:
		groups.append([group.get("token"), group.get("removal_pending", false)])
	var aids: Array = []
	for entry in state.aids:
		aids.append([entry["def"].id, entry["uses"]])
	return var_to_str([state.turn, state.year, state.phase, state.run_status, state.budget,
		state.political_capital, state.indicators, state.deck, state.hand, state.discard,
		state.flags, state.pending_event, state.played_cards_this_turn, modifiers, groups, aids,
		ui._flight_active])


func _observe() -> Dictionary:
	var fingerprint := _fingerprint()
	if fingerprint != _last_fingerprint:
		_revision += 1
		_last_fingerprint = fingerprint
	var result := {"protocol": VERSION, "revision": _revision, "legal_actions": [], "screen": "menu"}
	var actions: Array = result["legal_actions"]
	var controller: GameController = _main.controller
	var ui := _ui()
	if ui == null:
		var scenarios: Array = []
		for scenario: ScenarioDef in controller.available_scenarios():
			scenarios.append({"id": String(scenario.id), "name": scenario.display_name,
				"description": scenario.description, "goal": scenario.goal.description})
			actions.append({"kind": "start_run", "scenario": String(scenario.id)})
		result["scenarios"] = scenarios
		return result
	var sim: Simulation = controller.simulation
	var state := sim.state
	var scenario: ScenarioDef = controller.scenarios[state.scenario_id]
	result.merge({"screen": "game", "scenario": String(state.scenario_id), "year": state.year,
		"turn": state.turn, "turn_in_year": state.turn_in_year, "run_status": state.run_status,
		"budget": state.budget, "political_capital": state.political_capital,
		"annual_income": sim.annual_income(), "deck_count": state.deck.size(),
		"discard_count": state.discard.size(), "indicators": state.indicators.duplicate(),
		"goal": scenario.goal.description, "loss_reason": state.flags.get("loss_reason", ""),
		"flight_active": ui._flight_active, "hand": [], "modifiers": [], "aids": [], "cabinets": [],
		"pending_rewards": [], "played_cards": []})
	var goals: Array = []
	for condition in scenario.goal.conditions:
		goals.append({"text": TextFormatter.condition_text(controller.labels, condition), "met": condition.evaluate(state, sim.economy)})
	result["goal_conditions"] = goals
	var risks: Array = []
	for condition in scenario.goal.loss_conditions:
		risks.append(TextFormatter.condition_text(controller.labels, condition))
	result["failure_conditions"] = risks
	for id in state.hand:
		var card := _card(id)
		card["current_costs"] = sim.card_costs(id)
		result["hand"].append(card)
	for id in state.played_cards_this_turn:
		result["played_cards"].append(_card(id))
	for cabinet in state.cabinets:
		result["cabinets"].append({"id": String(cabinet.id), "name": cabinet.display_name, "description": cabinet.description})
	for modifier in state.modifiers:
		var description := String(modifier.source)
		var counter := ""
		if controller.statuses.has(modifier.target):
			var definition: StatusDef = controller.statuses[modifier.target]
			description = definition.description
			counter = StatusRules.counter(definition, state)
		result["modifiers"].append({"target": String(modifier.target), "scope": String(modifier.scope),
			"source": String(modifier.source), "operation": Modifier.op_label(modifier.op),
			"value": modifier.value, "stacks": modifier.stacks, "remaining_turns": modifier.remaining_turns,
			"active": modifier.is_active(state.turn), "description": description, "counter": counter})
	for entry in state.aids:
		var aid: AidDef = entry["def"]
		result["aids"].append({"id": String(aid.id), "name": aid.display_name,
			"effects": TextFormatter.effects_text(controller.labels, aid.effects)})
	if state.run_status != GameState.RunStatus.RUNNING or ui._flight_active:
		return result
	if not state.flags.get("pending_discard_draw", []).is_empty():
		result["decision"] = "discard_draw"
		_add_hand_actions(actions, "choose_discard_card", false)
	elif state.flags.get("pending_retain", false):
		result["decision"] = "retain"
		_add_hand_actions(actions, "choose_retained_card", true)
	elif state.flags.get("pending_review", false):
		result["decision"] = "review"
		_add_hand_actions(actions, "choose_review_card", true)
	elif state.pending_event != &"":
		result["decision"] = "event"
		var event: EconomyEventDef = controller.events[state.pending_event]
		result["event"] = {"name": event.title, "description": event.description, "choices": []}
		if event.choices.is_empty():
			if sim.event_choice_available(-1):
				actions.append({"kind": "resolve_event", "choice_index": -1})
			result["event"]["effects"] = TextFormatter.effects_text(controller.labels, sim.event_effects(event.effects))
		for index in event.choices.size():
			var choice: EventChoice = event.choices[index]
			result["event"]["choices"].append({"index": index, "label": choice.label,
				"description": choice.description, "effects": TextFormatter.effects_text(controller.labels, sim.event_effects(choice.effects))})
			if sim.event_choice_available(index):
				actions.append({"kind": "resolve_event", "choice_index": index})
	elif not state.pending_rewards.is_empty():
		result["decision"] = "rewards"
		var removal_index := -1
		for index in state.pending_rewards.size():
			if state.pending_rewards[index].get("removal_pending", false):
				removal_index = index
		for index in state.pending_rewards.size():
			var group: Dictionary = state.pending_rewards[index]
			var token := str(group.get("token", ""))
			var item := {"index": index, "token": token, "type": group["type"],
				"turn_reward": group.get("turn_reward", false), "options": [],
				"removal_pending": group.get("removal_pending", false)}
			for option_index in group["options"].size():
				var option: Reward = group["options"][option_index]
				item["options"].append(_reward(option, group))
				if removal_index < 0 and sim.reward_option_available(group, option):
					actions.append({"kind": "claim_reward", "group_index": index, "option_index": option_index, "token": token})
			if removal_index < 0:
				actions.append({"kind": "claim_reward", "group_index": index, "option_index": -1, "token": token})
			elif removal_index == index:
				var removable: Array = []
				for id in sim.removable_cards():
					if not removable.has(id):
						removable.append(id)
						item.get_or_add("removable_cards", []).append(_card(id))
						actions.append({"kind": "choose_removed_card", "group_index": index, "token": token, "card_id": String(id)})
				actions.append({"kind": "choose_removed_card", "group_index": index, "token": token, "card_id": ""})
			result["pending_rewards"].append(item)
	else:
		result["decision"] = "main"
		var offered: Array = []
		for id in state.hand:
			var costs := sim.card_costs(id)
			if not offered.has(id) and not ui._hand_card_disabled(costs):
				actions.append({"kind": "play_card", "card_id": String(id)})
				offered.append(id)
		for entry in state.aids:
			var id: StringName = entry["def"].id
			if not offered.has(id):
				actions.append({"kind": "use_aid", "aid_id": String(id)})
				offered.append(id)
		actions.append({"kind": "end_turn"})
		result["forecast"] = EconomyForecast.text(sim, controller.labels)
	return result


func _add_hand_actions(actions: Array, kind: String, skippable: bool) -> void:
	if skippable:
		actions.append({"kind": kind, "hand_index": -1})
	for index in _main.controller.simulation.state.hand.size():
		actions.append({"kind": kind, "hand_index": index})


func _act(request: Dictionary) -> Dictionary:
	var observation := _observe()
	if int(request.get("revision", -1)) != _revision:
		return {"ok": false, "error": "stale_observation", "observation": observation}
	var action = request.get("action", {})
	var allowed := false
	if action is Dictionary:
		for candidate in observation["legal_actions"]:
			# JSON parses numbers as floats; normalize both sides before deep comparison.
			if JSON.parse_string(JSON.stringify(candidate)) == action:
				allowed = true
				break
	if not allowed:
		return {"ok": false, "error": "illegal_action", "observation": observation}
	var ui := _ui()
	var command: Command
	match action["kind"]:
		"start_run":
			_main._on_start_requested(StringName(action["scenario"]))
		"play_card":
			var id := StringName(action["card_id"])
			for button in ui._hand_box.get_children():
				if button is CardButton and button.preview_card.id == id and not button.disabled:
					button.pressed.emit()
					break
		"end_turn": command = Command.end_turn()
		"use_aid": command = Command.use_aid(StringName(action["aid_id"]))
		"resolve_event": command = Command.resolve_event(int(action["choice_index"]))
		"claim_reward": command = Command.claim_reward(int(action["group_index"]), int(action["option_index"]), str(action["token"]))
		"choose_removed_card": command = Command.choose_removed_card(int(action["group_index"]), str(action["token"]), StringName(action["card_id"]))
		"choose_retained_card": command = Command.choose_retained_card(int(action["hand_index"]))
		"choose_review_card": command = Command.choose_review_card(int(action["hand_index"]))
		"choose_discard_card": command = Command.choose_discard_card(int(action["hand_index"]))
	if command != null:
		if action["kind"] == "claim_reward":
			ui._reward_group_index = int(action["group_index"])
			ui._overlay_signature = GameUI.UNSET_SIGNATURE
			ui._render_overlay()
			await create_timer(0.6).timeout
			var fresh := _observe()
			if int(request["revision"]) != _revision:
				return {"ok": false, "error": "stale_observation", "observation": fresh}
		ui._dispatch(command)
	for frame in 6:
		await process_frame
	while is_instance_valid(_ui()) and _ui()._flight_active:
		await process_frame
	return {"ok": true, "observation": _observe()}
