#!/bin/bash
set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}GrowAssistant-Bridge Development Setup${NC}"
echo "======================================"
echo ""

# Check Python version
echo -e "${YELLOW}Checking Python version...${NC}"
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
REQUIRED_VERSION="3.8"

if [[ $(echo -e "$REQUIRED_VERSION\n$PYTHON_VERSION" | sort -V | head -n1) != "$REQUIRED_VERSION" ]]; then
    echo -e "${RED}Error: Python 3.8+ is required. Found Python $PYTHON_VERSION${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Python $PYTHON_VERSION${NC}"
echo ""

# Create virtual environment if it doesn't exist
echo -e "${YELLOW}Setting up virtual environment...${NC}"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo -e "${GREEN}✓ Virtual environment created${NC}"
else
    echo -e "${GREEN}✓ Virtual environment already exists${NC}"
fi
echo ""

# Activate virtual environment
echo -e "${YELLOW}Activating virtual environment...${NC}"
source .venv/bin/activate
echo -e "${GREEN}✓ Virtual environment activated${NC}"
echo ""

# Upgrade pip
echo -e "${YELLOW}Upgrading pip...${NC}"
python3 -m pip install --upgrade pip > /dev/null 2>&1
echo -e "${GREEN}✓ pip upgraded${NC}"
echo ""

# Install requirements
echo -e "${YELLOW}Installing production dependencies...${NC}"
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt > /dev/null 2>&1
    echo -e "${GREEN}✓ Production dependencies installed${NC}"
else
    echo -e "${RED}Error: requirements.txt not found${NC}"
    exit 1
fi
echo ""

# Install dev requirements
echo -e "${YELLOW}Installing development dependencies...${NC}"
if [ -f "requirements-dev.txt" ]; then
    pip install -r requirements-dev.txt > /dev/null 2>&1
    echo -e "${GREEN}✓ Development dependencies installed${NC}"
else
    echo -e "${YELLOW}Warning: requirements-dev.txt not found (optional)${NC}"
fi
echo ""

# Copy config.example.yaml to config.yaml if it doesn't exist
echo -e "${YELLOW}Setting up configuration...${NC}"
if [ ! -f "config.yaml" ]; then
    if [ -f "config.example.yaml" ]; then
        cp config.example.yaml config.yaml
        echo -e "${GREEN}✓ Created config.yaml from config.example.yaml${NC}"
        echo -e "${YELLOW}  Note: Please update config.yaml with your settings${NC}"
    else
        echo -e "${YELLOW}Warning: config.example.yaml not found${NC}"
    fi
else
    echo -e "${GREEN}✓ config.yaml already exists${NC}"
fi
echo ""

# Install pre-commit hooks
echo -e "${YELLOW}Installing pre-commit hooks...${NC}"
if command -v pre-commit &> /dev/null; then
    pre-commit install > /dev/null 2>&1
    echo -e "${GREEN}✓ Pre-commit hooks installed${NC}"
else
    echo -e "${YELLOW}Warning: pre-commit not available${NC}"
fi
echo ""

# Run tests to verify setup
echo -e "${YELLOW}Running tests to verify setup...${NC}"
echo ""
if pytest tests/ -v --tb=short 2>&1 | head -50; then
    echo ""
    echo -e "${GREEN}✓ Tests passed${NC}"
else
    echo ""
    echo -e "${YELLOW}Warning: Some tests failed. Check the output above.${NC}"
fi
echo ""

echo -e "${GREEN}Setup complete!${NC}"
echo ""
echo "Next steps:"
echo "  1. Update config.yaml with your settings"
echo "  2. Run: make run"
echo "  3. Or run: source .venv/bin/activate && python3 -m app.main"
echo ""
