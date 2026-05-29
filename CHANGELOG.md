# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- GitHub release preparation (LICENSE, README, docs, examples)
- pyproject.toml for modern Python packaging
- install.sh one-click installation script
- check_env.py environment verification script
- Architecture documentation (docs/architecture.md)
- Quick start guide (docs/quickstart.md)
- Configuration guide (docs/configuration.md)
- Usage examples (examples/)

## [1.0.0] - 2026-05-30

### Added
- **Kernel (kernel.py)**: Central coordinator for all subsystems
- **OODA Loop (ooda_loop.py)**: Observe-Orient-Decide-Act decision cycle
- **Planner (planner.py)**: LLM-driven task decomposition and plan generation
- **PolicyEngine (policy_engine.py)**: Configurable security policies and risk control
- **MemoryManager (memory_manager.py)**: Five-layer memory system
  - Semantic memory (vector-based retrieval)
  - Episodic memory (event sequences)
  - Procedural memory (learned patterns)
  - Environmental memory (context state)
  - Index memory (fast lookup)
- **DriftAnalyzer (drift_analyzer.py)**: Behavior drift detection and policy adaptation
- **EventBus (event_bus.py)**: Publish-subscribe event system
- **EventLogger (event_logger.py)**: Event sourcing with full audit trail
- **Telemetry (telemetry.py)**: Performance metrics and health monitoring
- **ReflectionEngine (reflection_engine.py)**: Self-reflection and strategy optimization
- **ToolRegistry (tool_registry.py)**: Tool registration and capability discovery
- **WorldModel (world_model.py)**: Environment state modeling and prediction
- **StateManager (state_manager.py)**: System state persistence and recovery
- **GoalManager (goal_manager.py)**: Goal tracking and priority management
- **Watchdog (watchdog.py)**: Health monitoring and auto-recovery
- **RecoveryManager (recovery_manager.py)**: Failure recovery and rollback
- **ExperienceManager (experience_manager.py)**: Experience accumulation and learning
- **SelfObservation (self_observation.py)**: Self-monitoring loop
- **RuntimeSupervisor (runtime_supervisor.py)**: Runtime lifecycle management
- **TaskGraph (task_graph.py)**: Task dependency graph
- **PlanExecutor (plan_executor.py)**: Plan execution engine
- **FieldRunner (field_runner.py)**: End-to-end task execution
- **SemanticRetrieval (semantic_retrieval.py)**: Vector-based semantic search
- **TelemetryReplay (telemetry_replay.py)**: Telemetry data replay
- **DB Schema (db_schema.py)**: Database schema management
- **Exceptions (exceptions.py)**: Custom exception hierarchy
- **CLI (cli.py)**: Command-line interface

### Changed
- N/A (initial release)

### Deprecated
- N/A (initial release)

### Removed
- N/A (initial release)

### Fixed
- N/A (initial release)

### Security
- PolicyEngine enforces hardline blocklist for dangerous commands
- No hardcoded credentials in codebase
- No eval/exec usage
- Thread-safe SQLite connection pooling

## [0.1.0] - 2025-05-19

### Added
- Initial project structure
- Core module scaffolding
- Basic test framework

---

## Release Notes

### v1.0.0 - First Production Release

This is the first production-ready release of hermes-cognitive, featuring:

- **27 core modules** fully implemented and activated
- **251 tests** all passing (100% pass rate)
- **96.7% type annotation coverage**
- **8.82/10 code quality score**
- **Complete OODA decision loop** with LLM integration
- **Adaptive policy system** with drift detection feedback
- **Five-layer memory architecture** for comprehensive knowledge management
- **Event sourcing** with full audit trail and replay capability
- **Plugin system** for extensibility

### Upgrade Notes

This is the initial release. No upgrade path needed.

### Known Limitations

1. **Semantic Retrieval**: In-memory index only (no persistent vector DB yet)
2. **Multi-Agent**: Single-agent focused (multi-agent coordination planned)
3. **LLM Integration**: Currently supports OpenAI-compatible APIs only
4. **GUI**: No graphical interface (CLI only)

### Roadmap

- v1.1.0: Persistent semantic index (FAISS/ChromaDB)
- v1.2.0: Multi-agent coordination
- v1.3.0: Web UI dashboard
- v2.0.0: Distributed deployment support
