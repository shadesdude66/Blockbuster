# Bash completion for the `blockbuster` command.
# Source this file (e.g. from ~/.bashrc):
#   source /path/to/Blockbuster/completions/blockbuster.bash

_blockbuster_completions() {
    local cur prev
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$prev" == "--export-obsidian" ]]; then
        COMPREPLY=($(compgen -d -- "$cur"))
        return
    fi
    COMPREPLY=($(compgen -W "-h --help --version --export-obsidian" -- "$cur"))
}

complete -o filenames -F _blockbuster_completions blockbuster
