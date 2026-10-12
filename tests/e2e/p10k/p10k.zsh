# Minimal, deterministic Powerlevel10k configuration for the E2E container.
#
# This file is committed on purpose: the p10k scenario must not depend on the
# maintainer's own ~/.p10k.zsh. It sets only what the lion_watch segment needs
# and keeps startup silent (no configuration wizard, no gitstatus, no update
# prompt, no font/network probing), so the instant-prompt warning can be
# asserted reliably. Powerlevel10k itself is pinned to v1.20.0 in the
# Dockerfile next to this file.

# Temporarily change options, mirroring the generated config scaffolding.
'builtin' 'local' '-a' 'p10k_config_opts'
[[ ! -o 'aliases'         ]] || p10k_config_opts+=('aliases')
[[ ! -o 'sh_glob'         ]] || p10k_config_opts+=('sh_glob')
[[ ! -o 'no_brace_expand' ]] || p10k_config_opts+=('no_brace_expand')
'builtin' 'setopt' 'no_aliases' 'no_sh_glob' 'brace_expand'

() {
  emulate -L zsh -o extended_glob

  # Apply configuration changes without a shell restart.
  unset -m '(POWERLEVEL9K_*|DEFAULT_USER)~POWERLEVEL9K_GITSTATUS_DIR'

  # Zsh >= 5.1 is required.
  [[ $ZSH_VERSION == (5.<1->*|<6->.*) ]] || return

  # A directory-only left prompt and time-only right prompt: the integration
  # appends `lion_watch` to the right prompt itself. Keep the element lists
  # tiny so the `●` cannot be confused with any other segment's output.
  typeset -g POWERLEVEL9K_LEFT_PROMPT_ELEMENTS=(dir)
  typeset -g POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS=(time)

  # ASCII mode leaves the literal `●` from the lion_watch segment untouched.
  typeset -g POWERLEVEL9K_MODE=ascii
  # Keep the instant prompt verbose so its warning would be visible if any
  # startup output slipped through — the scenario asserts it never does.
  typeset -g POWERLEVEL9K_INSTANT_PROMPT=verbose
  # No wizard, no gitstatus (it would probe repositories and prompt to build),
  # no transient prompt and no hot reload: deterministic and silent.
  typeset -g POWERLEVEL9K_DISABLE_CONFIGURATION_WIZARD=true
  typeset -g POWERLEVEL9K_DISABLE_GITSTATUS=true
  typeset -g POWERLEVEL9K_TRANSIENT_PROMPT=off
  typeset -g POWERLEVEL9K_DISABLE_HOT_RELOAD=true
}

(( ${#p10k_config_opts} )) && setopt ${p10k_config_opts[@]}
'builtin' 'unset' 'p10k_config_opts'
