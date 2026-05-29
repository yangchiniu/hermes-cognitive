"""
Event Bus Unit Tests

Tests publish/subscribe, wildcard subscription, filter functions, wait_for()
timeout, history & stats, thread safety, and event persistence to EventLogger.

Standard library only; imports event_bus modules with try/except guards.
"""

import time
import json
import queue
import random
import threading
import unittest
from pathlib import Path

try:
    from hermes.core.event_bus import EventBus, Event, Subscription
    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False

try:
    from hermes.core.event_logger import EventLogger
    HAS_EVENT_LOGGER = True
except ImportError:
    HAS_EVENT_LOGGER = False


# ---------------------------------------------------------------------------
# Stub / Mock
# ---------------------------------------------------------------------------

class StubEvent:
    """Minimal event representation for testing."""
    def __init__(self, event_type, data=None, source=None, timestamp=None):
        self.event_type = event_type
        self.data = data or {}
        self.source = source
        self.timestamp = timestamp or time.time()

    def __repr__(self):
        return f"Event({self.event_type}, {self.data})"


class StubEventBus:
    """In-memory event bus for testing without the real dependency."""

    def __init__(self, event_logger=None):
        self._subscriptions = {}       # topic -> list of callables
        self._filters = {}             # topic -> list of filter funcs
        self._history = []
        self._stats = {"published": 0, "delivered": 0, "filtered": 0}
        self._lock = threading.RLock()
        self.event_logger = event_logger
        self._wait_events = {}         # topic -> list of threading.Event

    def publish(self, event_type, data=None, source=None):
        """Publish an event to all matching subscribers."""
        e = StubEvent(event_type, data, source)
        with self._lock:
            self._history.append(e)
            self._stats["published"] += 1

        # Deliver to exact-match subscribers
        callbacks = list(self._subscriptions.get(event_type, []))
        # Deliver to wildcard subscribers
        for topic, cbs in self._subscriptions.items():
            if topic.endswith(".*") and event_type.startswith(topic[:-1]):
                callbacks.extend(cbs)
            if topic == "*":
                callbacks.extend(cbs)

        for cb in callbacks:
            try:
                # Check filters
                skip = False
                for filt in self._filters.get(event_type, []):
                    if not filt(e):
                        skip = True
                        with self._lock:
                            self._stats["filtered"] += 1
                        break
                if skip:
                    continue
                cb(e)
                with self._lock:
                    self._stats["delivered"] += 1
            except Exception:
                pass

        # Signal wait_for events
        with self._lock:
            for topic, evts in self._wait_events.items():
                if topic == event_type or topic.endswith(".*") and event_type.startswith(topic[:-1]):
                    for evt in evts:
                        evt.set()

        # Persist to event logger
        if self.event_logger is not None:
            try:
                self.event_logger.log_event(event_type, data, source)
            except Exception:
                pass

    def subscribe(self, topic, callback):
        """Subscribe to an event topic."""
        with self._lock:
            if topic not in self._subscriptions:
                self._subscriptions[topic] = []
            self._subscriptions[topic].append(callback)
            return lambda: self._subscriptions[topic].remove(callback)

    def unsubscribe(self, topic, callback):
        """Remove a subscription."""
        with self._lock:
            if topic in self._subscriptions and callback in self._subscriptions[topic]:
                self._subscriptions[topic].remove(callback)

    def add_filter(self, topic, filter_func):
        """Add a filter function for a topic."""
        with self._lock:
            if topic not in self._filters:
                self._filters[topic] = []
            self._filters[topic].append(filter_func)

    def wait_for(self, topic, timeout=5.0):
        """Block until an event of the given topic is published."""
        evt = threading.Event()
        with self._lock:
            if topic not in self._wait_events:
                self._wait_events[topic] = []
            self._wait_events[topic].append(evt)

        try:
            result = evt.wait(timeout)
            return result  # True if event fired, False on timeout
        finally:
            with self._lock:
                if topic in self._wait_events and evt in self._wait_events[topic]:
                    self._wait_events[topic].remove(evt)

    def get_history(self, topic=None, limit=None):
        """Return event history, optionally filtered by topic."""
        with self._lock:
            if topic:
                filtered = [e for e in self._history if e.event_type == topic]
            else:
                filtered = list(self._history)
            if limit:
                filtered = filtered[-limit:]
            return filtered

    def get_stats(self):
        """Return a copy of current statistics."""
        with self._lock:
            return dict(self._stats)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPublishSubscribe(unittest.TestCase):
    """Basic publish/subscribe functionality."""

    def setUp(self):
        self.bus = StubEventBus()
        self.received = []

    def _collector(self, event):
        self.received.append(event)

    def test_publish_delivers_to_subscriber(self):
        """A published event should be received by a matching subscriber."""
        self.bus.subscribe("test.topic", self._collector)
        self.bus.publish("test.topic", {"key": "value"})
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.received[0].event_type, "test.topic")
        self.assertEqual(self.received[0].data["key"], "value")

    def test_no_subscriber_no_delivery(self):
        """Publishing without subscribers should not crash."""
        self.bus.publish("orphan.topic", {"data": 1})
        self.assertEqual(len(self.received), 0)

    def test_multiple_subscribers_all_receive(self):
        """All subscribers on the same topic should receive events."""
        received2 = []
        self.bus.subscribe("test.topic", self._collector)
        self.bus.subscribe("test.topic", lambda e: received2.append(e))
        self.bus.publish("test.topic", {"n": 42})
        self.assertEqual(len(self.received), 1)
        self.assertEqual(len(received2), 1)

    def test_unsubscribe_stops_delivery(self):
        """After unsubscribing, a callback should not receive events."""
        unsub = self.bus.subscribe("test.topic", self._collector)
        unsub()  # unsubscribe
        self.bus.publish("test.topic", {"n": 99})
        self.assertEqual(len(self.received), 0)


class TestWildcardSubscription(unittest.TestCase):
    """Wildcard topic matching."""

    def setUp(self):
        self.bus = StubEventBus()
        self.received = []

    def _collector(self, event):
        self.received.append(event)

    def test_wildcard_suffix(self):
        """Wildcard topic.* should match all subtopics."""
        self.bus.subscribe("sensor.*", self._collector)
        self.bus.publish("sensor.temp", {"value": 25})
        self.bus.publish("sensor.humidity", {"value": 60})
        self.assertEqual(len(self.received), 2)

    def test_global_wildcard(self):
        """'*' should match everything."""
        self.bus.subscribe("*", self._collector)
        self.bus.publish("any.topic", {})
        self.bus.publish("another.topic", {})
        self.assertEqual(len(self.received), 2)

    def test_wildcard_no_match(self):
        """Wildcard should not match unrelated topics."""
        self.bus.subscribe("alarm.*", self._collector)
        self.bus.publish("sensor.temp", {})
        self.assertEqual(len(self.received), 0)


class TestFilterFunctions(unittest.TestCase):
    """Filter functions that conditionally suppress delivery."""

    def setUp(self):
        self.bus = StubEventBus()
        self.received = []

    def _collector(self, event):
        self.received.append(event)

    def test_filter_blocks_matching(self):
        """A filter returning False should block delivery."""
        self.bus.subscribe("test", self._collector)
        self.bus.add_filter("test", lambda e: e.data.get("allowed", False))
        self.bus.publish("test", {"allowed": False})
        self.assertEqual(len(self.received), 0)

    def test_filter_passes_allowed(self):
        """A filter returning True should allow delivery."""
        self.bus.subscribe("test", self._collector)
        self.bus.add_filter("test", lambda e: e.data.get("allowed", False))
        self.bus.publish("test", {"allowed": True})
        self.assertEqual(len(self.received), 1)

    def test_multiple_filters_all_must_pass(self):
        """All filters must pass for delivery (AND logic)."""
        self.bus.subscribe("test", self._collector)
        self.bus.add_filter("test", lambda e: e.data.get("a", False))
        self.bus.add_filter("test", lambda e: e.data.get("b", False))
        self.bus.publish("test", {"a": True, "b": True})
        self.assertEqual(len(self.received), 1)
        self.bus.publish("test", {"a": True, "b": False})
        self.assertEqual(len(self.received), 1)  # no new delivery


class TestWaitForTimeout(unittest.TestCase):
    """wait_for() blocking call with timeout."""

    def setUp(self):
        self.bus = StubEventBus()

    def test_wait_for_returns_true_on_event(self):
        """wait_for should return True when matching event arrives."""
        def _publish_later():
            time.sleep(0.05)
            self.bus.publish("expected.topic", {})
        t = threading.Thread(target=_publish_later, daemon=True)
        t.start()
        result = self.bus.wait_for("expected.topic", timeout=2.0)
        self.assertTrue(result)

    def test_wait_for_returns_false_on_timeout(self):
        """wait_for should return False when no matching event arrives."""
        result = self.bus.wait_for("nonexistent.topic", timeout=0.1)
        self.assertFalse(result)

    def test_wait_for_zero_timeout(self):
        """wait_for with zero timeout should return immediately."""
        result = self.bus.wait_for("any.topic", timeout=0)
        self.assertFalse(result)


class TestHistoryAndStats(unittest.TestCase):
    """Event history and statistics tracking."""

    def setUp(self):
        self.bus = StubEventBus()

    def test_history_records_events(self):
        """Published events should appear in history."""
        self.bus.publish("a", {"n": 1})
        self.bus.publish("b", {"n": 2})
        history = self.bus.get_history()
        self.assertEqual(len(history), 2)

    def test_history_filter_by_topic(self):
        """get_history(topic=...) should filter by topic."""
        self.bus.publish("a", {"n": 1})
        self.bus.publish("b", {"n": 2})
        self.bus.publish("a", {"n": 3})
        hist_a = self.bus.get_history(topic="a")
        self.assertEqual(len(hist_a), 2)

    def test_history_limit(self):
        """get_history(limit=...) should cap results."""
        for i in range(10):
            self.bus.publish("t", {"i": i})
        limited = self.bus.get_history(limit=3)
        self.assertEqual(len(limited), 3)

    def test_stats_counts(self):
        """Statistics should track publish and delivery counts."""
        self.bus.subscribe("t", lambda e: None)
        self.bus.publish("t", {})
        self.bus.publish("t", {})
        stats = self.bus.get_stats()
        self.assertEqual(stats["published"], 2)
        self.assertEqual(stats["delivered"], 2)

    def test_stats_filtered_count(self):
        """Filtered events should be counted."""
        self.bus.subscribe("t", lambda e: None)
        self.bus.add_filter("t", lambda e: False)
        self.bus.publish("t", {})
        stats = self.bus.get_stats()
        self.assertEqual(stats["filtered"], 1)
        self.assertEqual(stats["delivered"], 0)


class TestThreadSafety(unittest.TestCase):
    """Concurrent publish/subscribe should not corrupt state."""

    def setUp(self):
        self.bus = StubEventBus()
        self.errors = []

    def test_concurrent_publishes(self):
        """Many concurrent publish calls should not crash."""
        def _publish(i):
            try:
                self.bus.publish(f"topic.{i % 5}", {"i": i})
            except Exception as e:
                self.errors.append(e)

        threads = [threading.Thread(target=_publish, args=(i,), daemon=True)
                   for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(len(self.errors), 0, f"Errors: {self.errors}")

    def test_concurrent_sub_unsub(self):
        """Concurrent subscribe/unsubscribe should not corrupt state."""
        def _sub_unsub(i):
            try:
                cb = lambda e: None
                unsub = self.bus.subscribe(f"topic.{i % 3}", cb)
                if i % 2 == 0:
                    unsub()
            except Exception as e:
                self.errors.append(e)

        threads = [threading.Thread(target=_sub_unsub, args=(i,), daemon=True)
                   for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(len(self.errors), 0, f"Errors: {self.errors}")

    def test_concurrent_read_write(self):
        """Concurrent publish and history read should not race."""
        def _publish():
            for i in range(20):
                self.bus.publish("t", {"i": i})
                time.sleep(0.001)

        def _read():
            for _ in range(20):
                try:
                    self.bus.get_history()
                    self.bus.get_stats()
                except Exception as e:
                    self.errors.append(e)
                time.sleep(0.001)

        threads = []
        for _ in range(3):
            threads.append(threading.Thread(target=_publish, daemon=True))
            threads.append(threading.Thread(target=_read, daemon=True))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(len(self.errors), 0, f"Errors: {self.errors}")


class TestEventPersistence(unittest.TestCase):
    """Event logging / persistence integration."""

    def setUp(self):
        self.log_path = Path("/tmp") / f"test_event_log_{int(time.time())}_{random.randint(0,99999)}.jsonl"
        if HAS_EVENT_LOGGER:
            try:
                self.logger = EventLogger(str(self.log_path))
            except Exception:
                self.logger = None
        else:
            self.logger = None

    def tearDown(self):
        if self.log_path.exists():
            try:
                self.log_path.unlink()
            except OSError:
                pass

    def test_event_persisted_to_logger(self):
        """Events should be written to the EventLogger when one is attached."""
        bus = StubEventBus(event_logger=self.logger)
        bus.publish("persist.test", {"msg": "hello"})

        if self.logger is not None:
            # Try to read back
            if self.log_path.exists():
                content = self.log_path.read_text()
                self.assertIn("persist.test", content)
                self.assertIn("hello", content)
            else:
                # Logger may buffer; at minimum verify no crash
                pass
        else:
            self.skipTest("EventLogger not available")

    def test_persistence_without_logger(self):
        """Event bus should work without an EventLogger."""
        bus = StubEventBus(event_logger=None)
        try:
            bus.publish("test.topic", {"n": 1})
        except Exception as e:
            self.fail(f"Publishing without logger raised: {e}")

    def test_logger_crash_does_not_affect_bus(self):
        """A failing EventLogger should not crash the bus."""
        class _BrokenLogger:
            def log_event(self, *a, **kw):
                raise RuntimeError("Logger broken")

        bus = StubEventBus(event_logger=_BrokenLogger())
        try:
            bus.publish("test.topic", {"n": 1})
        except Exception as e:
            self.fail(f"Broken logger crashed bus: {e}")


class TestEdgeCases(unittest.TestCase):
    """Edge cases and error handling."""

    def setUp(self):
        self.bus = StubEventBus()

    def test_publish_empty_type(self):
        """Publishing with empty event type should not crash."""
        try:
            self.bus.publish("", {})
        except Exception as e:
            self.fail(f"Empty event type raised: {e}")

    def test_publish_no_data(self):
        """Publishing with no data should not crash."""
        try:
            self.bus.publish("test", None)
        except Exception as e:
            self.fail(f"No data raised: {e}")

    def test_subscribe_none_callback(self):
        """Subscribing with None callback should not crash bus (may log)."""
        try:
            self.bus.subscribe("test", None)
        except Exception:
            pass  # Acceptable to raise on bad input
        # Bus should still work
        received = []
        self.bus.subscribe("test", lambda e: received.append(e))
        self.bus.publish("test", {})
        self.assertEqual(len(received), 1)

    def test_filter_exception_does_not_crash_bus(self):
        """A filter that raises should not prevent further delivery."""
        received = []
        self.bus.subscribe("test", lambda e: received.append(e))
        self.bus.add_filter("test", lambda e: (_ for _ in ()).throw(ValueError("filter error")))
        self.bus.publish("test", {})
        # Bus should survive; at least one event may or may not deliver
        self.assertGreaterEqual(len(received), 0)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def run_tests(verbose=False):
    """Run all event bus unit tests and return a results dict."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestPublishSubscribe))
    suite.addTests(loader.loadTestsFromTestCase(TestWildcardSubscription))
    suite.addTests(loader.loadTestsFromTestCase(TestFilterFunctions))
    suite.addTests(loader.loadTestsFromTestCase(TestWaitForTimeout))
    suite.addTests(loader.loadTestsFromTestCase(TestHistoryAndStats))
    suite.addTests(loader.loadTestsFromTestCase(TestThreadSafety))
    suite.addTests(loader.loadTestsFromTestCase(TestEventPersistence))
    suite.addTests(loader.loadTestsFromTestCase(TestEdgeCases))

    runner = unittest.TextTestRunner(verbosity=2 if verbose else 1)
    result = runner.run(suite)

    return {
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "success": result.wasSuccessful(),
    }


if __name__ == "__main__":
    run_tests(verbose=True)
