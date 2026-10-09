"""Register the JSON story operation without changing the core server."""
from .. import checks, story


def register(mcp, project):
    project.register_op('add_story', story.add_story)
    project.map_edit = story.map_edit_wrapper(project, project.map_edit)
    checks.register_check('story', story.check_story)
