"""Register the mini-game map_edit operation and its static map check."""
from .. import checks, mechanics, minigames


def register(mcp, project):
    mechanics.register_fragment('minigame_game', minigames.DATA / 'game.js')
    project.register_op('add_minigame', minigames.add_minigame)
    project.map_edit = minigames.map_edit_wrapper(project, project.map_edit)
    minigames.register_check()
