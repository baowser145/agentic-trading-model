"""Tests for Discord message chunking and rate-limit handling (no network calls)."""
from unittest import mock

import pytest

import send_discord
from send_discord import DISCORD_MAX_LEN, post_chunk, split_message


class TestSplitMessage:
    def test_short_message_untouched(self):
        assert split_message("hello") == ["hello"]

    def test_exactly_at_limit_untouched(self):
        msg = "x" * DISCORD_MAX_LEN
        assert split_message(msg) == [msg]

    def test_splits_on_paragraph_boundaries(self):
        cards = [f"Card {i}\nline two of card {i}" for i in range(100)]
        msg = "\n\n".join(cards)
        chunks = split_message(msg)
        assert len(chunks) > 1
        assert all(len(c) <= DISCORD_MAX_LEN for c in chunks)
        # no card is ever cut in the middle: every card appears intact in exactly one chunk
        for card in cards:
            assert sum(c.count(card) for c in chunks) == 1

    def test_content_preserved(self):
        cards = [f"Card {i} " + "y" * 150 for i in range(30)]
        msg = "\n\n".join(cards)
        chunks = split_message(msg)
        assert "\n\n".join(chunks) == msg

    def test_single_oversized_paragraph_hard_split(self):
        msg = "z" * (DISCORD_MAX_LEN * 2 + 100)
        chunks = split_message(msg)
        assert all(len(c) <= DISCORD_MAX_LEN for c in chunks)
        assert "".join(chunks) == msg


def _response(status_code, retry_after=None):
    resp = mock.Mock()
    resp.status_code = status_code
    resp.headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = RuntimeError(f"HTTP {status_code}")
    return resp


class TestPostChunk:
    def test_success_posts_once(self):
        with mock.patch.object(send_discord.requests, "post", return_value=_response(204)) as post:
            post_chunk("https://example.invalid/hook", "msg")
        assert post.call_count == 1

    def test_retries_on_429_then_succeeds(self):
        responses = [_response(429, retry_after=0.01), _response(204)]
        with mock.patch.object(send_discord.requests, "post", side_effect=responses) as post, \
             mock.patch.object(send_discord.time, "sleep") as sleep:
            post_chunk("https://example.invalid/hook", "msg")
        assert post.call_count == 2
        sleep.assert_called_once_with(0.01)

    def test_gives_up_after_max_attempts(self):
        responses = [_response(429, retry_after=0.01)] * send_discord.MAX_ATTEMPTS_PER_CHUNK
        with mock.patch.object(send_discord.requests, "post", side_effect=responses), \
             mock.patch.object(send_discord.time, "sleep"):
            with pytest.raises(RuntimeError, match="rate-limiting"):
                post_chunk("https://example.invalid/hook", "msg")

    def test_non_429_error_raises_immediately(self):
        with mock.patch.object(send_discord.requests, "post", return_value=_response(404)) as post:
            with pytest.raises(RuntimeError, match="HTTP 404"):
                post_chunk("https://example.invalid/hook", "msg")
        assert post.call_count == 1

    def test_retry_after_capped(self):
        responses = [_response(429, retry_after=9999), _response(204)]
        with mock.patch.object(send_discord.requests, "post", side_effect=responses), \
             mock.patch.object(send_discord.time, "sleep") as sleep:
            post_chunk("https://example.invalid/hook", "msg")
        sleep.assert_called_once_with(30.0)
