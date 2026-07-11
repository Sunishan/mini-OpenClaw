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
printf "${PINK}        /\\_____/\\\n"
printf "${PINK}       /  o   o  \\\n"
printf "${PINK}      ( ==  ^  == )\n"
printf "${PINK}       )         (\n"
printf "${PINK}      (           )\n"
printf "${PINK}     ( (  )   (  ) )\n"
printf "${PINK}    (__(__)___(__)__)\n"
printf "${PINK}     |           |\n"
printf "${PINK}     |  -------  |\n"
printf "${PINK}     | |       | |\n"
printf "${PINK}     | |  mini | |\n"
printf "${PINK}     |_| OpenClaw|_|\n"
printf "\n"
printf "${CYAN}  +------------------------------------------+\n"
printf "${CYAN}  |  ${BOLD}${WHITE}mini-OpenClaw 智能助手${RESET}${CYAN}                  |\n"
printf "${CYAN}  |  ${DIM}直接输入自然语言任务即可开始${RESET}${CYAN}             |\n"
printf "${CYAN}  +------------------------------------------+\n"
printf "\n"
printf "  ${GREEN}>${RESET} 单次任务：${YELLOW}ask \"创建一个 hello.py 并运行\"${RESET}\n"
printf "  ${GREEN}>${RESET} 交互模式：${YELLOW}ask -i${RESET}\n"
printf "  ${GREEN}>${RESET} 自检：${YELLOW}check${RESET}\n"
printf "\n"
printf "  ${DIM}输入 exit 或 Ctrl+D 退出 shell${RESET}\n"
printf "\n"
