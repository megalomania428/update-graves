<system-reminder>
As you answer the user's questions, you can use the following context:
# gitStatus
This is the git status at the start of the conversation. Note that this status is a snapshot in time, and will not update during the conversation.

Current branch: {{crs_branch}}

Main branch (you will usually use this for PRs): {{crs_master}}

Git user: {{crs_repo_user}}

Status:
{{crs_repo_status_short}}

Recent commits:
{{crs_repo_recent_commits}}

Claude Code attached this context automatically; it isn't part of the user's message. It describes the user's own account and workspace, so they don't need it reported back.
</system-reminder>
