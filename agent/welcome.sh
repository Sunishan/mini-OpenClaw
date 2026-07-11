#!/bin/bash
# Welcome banner shown by deployment shells.

clear 2>/dev/null || true

PINK='\033[38;5;205m'
CYAN='\033[38;5;51m'
GREEN='\033[38;5;82m'
YELLOW='\033[38;5;227m'
WHITE='\033[38;5;255m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

printf "\n"
printf "%b" "$PINK"
cat <<'EOF'
        /\_____/\
       /  o   o  \
      ( ==  ^  == )
       )         (
      (           )
     ( (  )   (  ) )
    (__(__)___(__)__)
     |           |
     |  -------  |
     | |       | |
     | |  mini | |
     |_| OpenClaw|_|
EOF
printf "%b" "$RESET"
printf "\n"
printf "${CYAN}  ------------------------------------------${RESET}\n"
printf "  ${BOLD}${WHITE}mini-OpenClaw 智能助手${RESET}\n"
printf "  ${DIM}选择下面的命令入口开始使用${RESET}\n"
printf "${CYAN}  ------------------------------------------${RESET}\n"
printf "\n"
printf "  ${GREEN}>${RESET} 单次任务：${YELLOW}python -m agent.cli \"判断这个网页的可信度 https://example.com\"${RESET}\n"
printf "  ${GREEN}>${RESET} 多轮交互：${YELLOW}python -m agent.cli -i${RESET}\n"
printf "  ${GREEN}>${RESET} 系统自检：${YELLOW}python -m agent.cli --selfcheck${RESET}\n"
printf "\n"
printf "  ${DIM}单次任务请把自然语言问题写在命令末尾的引号里${RESET}\n"
printf "  ${DIM}进入多轮交互后，可以直接输入自然语言问题${RESET}\n"
printf "\n"
