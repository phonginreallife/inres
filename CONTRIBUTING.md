# Contributing to InRes

Thank you for your interest in contributing to InRes! This document provides guidelines and instructions for contributing.

## 📋 Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Pull Request Process](#pull-request-process)
- [Code Style Guidelines](#code-style-guidelines)
- [Testing](#testing)
- [Documentation](#documentation)

## Code of Conduct

By participating in this project, you agree to maintain a respectful and inclusive environment. Please be considerate of others and their contributions.

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/InRes.git
   cd InRes
   ```
3. **Add upstream remote**:
   ```bash
   git remote add upstream https://github.com/phonginreallife/InRes.git
   ```

## Development Setup

### Prerequisites

- Docker & Docker Compose
- Go 1.21+
- Python 3.11+
- Node.js 18+
- Supabase CLI

### Local Development

1. **Start Supabase locally**:
   ```bash
   cd supabase
   supabase start
   ```

2. **Configure environment**:
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

3. **Start backend services**:
   ```bash
   # API Server
   cd api && air
   
   # AI Agent
   cd server/agent && source venv/bin/activate && python claude_agent.py
   ```

4. **Start frontend**:
   ```bash
   cd frontend/inres && npm install && npm run dev
   ```

## Making Changes

1. **Create a feature branch**:
   ```bash
   git checkout -b feature/your-feature-name
   # or
   git checkout -b fix/your-bug-fix
   ```

2. **Make your changes** following our code style guidelines

3. **Test your changes** thoroughly

4. **Commit with clear messages**:
   ```bash
   git commit -m "feat: add new feature description"
   # or
   git commit -m "fix: resolve issue with X"
   ```

   We follow [Conventional Commits](https://www.conventionalcommits.org/):
   - `feat:` - New features
   - `fix:` - Bug fixes
   - `docs:` - Documentation changes
   - `chore:` - Maintenance tasks
   - `refactor:` - Code refactoring
   - `test:` - Adding tests

## Pull Request Process

1. **Update your branch** with the latest upstream changes:
   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

2. **Push your branch**:
   ```bash
   git push origin feature/your-feature-name
   ```

3. **Create a Pull Request** on GitHub with:
   - Clear title describing the change
   - Description of what and why
   - Reference to any related issues
   - Screenshots for UI changes

4. **Wait for review** - maintainers will review your PR

5. **Address feedback** if any changes are requested

## Code Style Guidelines

### Go (Backend API)

```bash
# Format code
go fmt ./...

# Run linter
go vet ./...

# Run tests
go test ./...
```

- Follow standard Go conventions
- Use meaningful variable and function names
- Add comments for exported functions
- Handle errors appropriately

### Python (AI Agent)

```bash
# Format with black (if installed)
black .

# Run type checking (if mypy installed)
mypy .
```

- Follow PEP 8 style guide
- Use type hints where possible
- Document functions with docstrings

### TypeScript/JavaScript (Frontend)

```bash
# Run linter
npm run lint

# Format code
npx prettier --write .
```

- Use TypeScript for new files
- Follow React best practices
- Use functional components and hooks

## Testing

### Backend Tests

```bash
cd api
go test ./... -v
```

### Frontend Tests

```bash
cd frontend/inres
npm test
```

### Integration Tests

Use Docker Compose for full-stack testing:

```bash
cd deploy/docker
docker compose up -d
# Run your integration tests
```

## Documentation

- Update README.md if adding new features
- Add inline code comments for complex logic
- Update API documentation for new endpoints
- Include JSDoc/GoDoc comments for public APIs

## Questions?

- Open a [GitHub Issue](https://github.com/phonginreallife/InRes/issues) for bugs or feature requests
- For security issues, please see [SECURITY.md](SECURITY.md)

---

Thank you for contributing to InRes! 🎉
