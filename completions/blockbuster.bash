# Bash completion for the `blockbuster` command.
# Source this file (e.g. from ~/.bashrc):
#   source /path/to/Blockbuster/completions/blockbuster.bash

_blockbuster_completions() {
    local cur
    cur="${COMP_WORDS[COMP_CWORD]}"
    COMPREPLY=($(compgen -W "--help --version" -- "$cur"))
}

complete -F _blockbuster_completions blockbuster
