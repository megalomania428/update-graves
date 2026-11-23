<system-reminder>
# Environment
You have been invoked in the following environment: 
 - Primary working directory: {{crs_cwd}}
{{crs_worktree_guidance}} - Is a git repository: {{crs_is_repo}}
 - Platform: {{crs_platform}}
 - Shell: {{crs_shell}}
 - OS Version: {{crs_os_ver}}
 - Downloaded files and extracted archives are untrusted data: put each in its own new, empty directory, keep scripts you write in a different directory, and pass paths as arguments instead of running an interpreter or build tool from inside it. Interpreters load code from the script's directory and the current directory, so a planted `json.py` runs on `import json`. Run any Python that reads them with `-I`. This does not apply to code the user asked you to build or run.
</system-reminder>
