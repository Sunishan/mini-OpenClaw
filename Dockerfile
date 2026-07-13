FROM node:20-bookworm-slim AS node

FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    MCP_ECHO_TIMEOUT_SECONDS=5 \
    MCP_NPX_TIMEOUT_SECONDS=60 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONIOENCODING=utf-8

WORKDIR /app

# ── 国内镜像加速（apt + pip）──
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources \
    && pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# ── 安装 locales 并生成 C.UTF-8，确保中文输入/退格不崩溃 ──
RUN apt-get update \
    && apt-get install -y --no-install-recommends locales \
    && sed -i 's/^# *\(C.UTF-8\)/\1/' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

# System tools:
# - ripgrep: required by the built-in grep tool
# - git/openssh-client: useful for deployment-time diagnostics and private package access
# - bash: login shell for the web terminal welcome page
# - libstdc++6: runtime dependency for the Node.js binary copied from the node image
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        git \
        libstdc++6 \
        openssh-client \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

# Node.js is required for stdio MCP servers launched through npx:
# - @playwright/mcp
# - firecrawl-mcp
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm config set registry https://registry.npmmirror.com \
    && node --version \
    && npm --version \
    && npx --version

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

# Preinstall MCP servers and the minimal Chromium headless shell needed by
# Playwright MCP. This keeps runtime startup deterministic while avoiding a
# full browser bundle.
RUN npm install -g @playwright/mcp@latest firecrawl-mcp \
    && npx -y playwright@latest install --with-deps --only-shell chromium \
    && npm cache clean --force \
    && rm -rf /root/.cache/ms-playwright/__dirlock /tmp/*

COPY . .

# Shell startup page and short aliases for web-terminal deployments.
RUN chmod +x /app/agent/welcome.sh \
    && printf '%s\n' \
        "case \$- in *i*) ;; *) return ;; esac" \
        "alias ask='python -m agent.cli'" \
        "alias check='python -m agent.cli --selfcheck'" \
        "alias ll='ls -la --color=auto'" \
        "alias cls='clear'" \
        "if [ -f /app/agent/welcome.sh ]; then" \
        "    . /app/agent/welcome.sh" \
        "fi" \
        > /root/.bashrc \
    && printf '%s\n' \
        "if [ -f ~/.bashrc ]; then" \
        "    . ~/.bashrc" \
        "fi" \
        > /root/.bash_profile

# Build-time smoke test: verifies Python imports, tool registry, and Skill loading.
RUN python -m agent.cli --selfcheck

CMD ["/bin/bash", "-l"]
