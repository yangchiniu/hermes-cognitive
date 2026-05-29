#!/usr/bin/env bash
# hermes-cognitive installation script
# Usage: chmod +x scripts/install.sh && ./scripts/install.sh

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# -------------------------------------------------------------------
# 1. Check Python version
# -------------------------------------------------------------------
info "Checking Python version..."
PYTHON_CMD=""
for cmd in python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    error "Python 3.11+ is required. Please install Python 3.11 or later."
fi
ok "Python found: $PYTHON_CMD ($($PYTHON_CMD --version))"

# -------------------------------------------------------------------
# 2. Create virtual environment (optional)
# -------------------------------------------------------------------
VENV_DIR=".venv"
if [ "${CREATE_VENV:-1}" = "1" ]; then
    if [ ! -d "$VENV_DIR" ]; then
        info "Creating virtual environment..."
        $PYTHON_CMD -m venv "$VENV_DIR"
        ok "Virtual environment created: $VENV_DIR"
    else
        info "Virtual environment already exists: $VENV_DIR"
    fi
    
    # Activate venv
    source "$VENV_DIR/bin/activate"
    ok "Virtual environment activated"
fi

# -------------------------------------------------------------------
# 3. Upgrade pip
# -------------------------------------------------------------------
info "Upgrading pip..."
pip install --upgrade pip -q

# -------------------------------------------------------------------
# 4. Install dependencies
# -------------------------------------------------------------------
info "Installing dependencies..."
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt -q
    ok "Core dependencies installed"
fi

# -------------------------------------------------------------------
# 5. Install package in development mode
# -------------------------------------------------------------------
info "Installing hermes-cognitive in development mode..."
pip install -e ".[dev]" -q
ok "hermes-cognitive installed"

# -------------------------------------------------------------------
# 6. Run environment check
# -------------------------------------------------------------------
info "Running environment check..."
if [ -f "scripts/check_env.py" ]; then
    python scripts/check_env.py
fi

# -------------------------------------------------------------------
# 7. Run tests
# -------------------------------------------------------------------
info "Running tests..."
if [ -f "tests/test_all.py" ]; then
    python tests/test_all.py
    ok "All tests passed"
fi

# -------------------------------------------------------------------
# Done
# -------------------------------------------------------------------
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  hermes-cognitive installation complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Quick start:"
echo "  source .venv/bin/activate  # if using venv"
echo "  python -c \"from hermes_core.core import core_initialize; core_initialize(); print('OK')\""
echo ""
echo "Documentation:"
echo "  docs/quickstart.md     - Getting started guide"
echo "  docs/architecture.md   - Architecture overview"
echo "  docs/configuration.md  - Configuration reference"
echo ""
