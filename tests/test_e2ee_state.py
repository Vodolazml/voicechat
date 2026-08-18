from app.server.e2ee_state import E2EEState


def test_channel_state_cleanup_timer_is_numeric() -> None:
    state = E2EEState()
    state.set_public_key(1, "public-key", "fingerprint")

    result = state.channel_state(channel_id=10, participant_ids={1}, requester_id=1)

    assert result["users"] == [{"user_id": 1, "public_key": "public-key", "fingerprint": "fingerprint"}]
    assert result["key_sets"] == []
