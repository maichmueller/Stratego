from stratego.learning import RewardToken
from stratego.engine.piece import Piece, Obstacle
from stratego.agent import Agent, RLAgent
from stratego.engine.state import State
from stratego.engine.logic import Logic
from stratego.engine.position import Position, Move
from stratego.engine.board import Board
from stratego.engine.game_defs import Status, Team, HookPoint, GameSpecification
from stratego.utils import slice_kwargs

from typing import Optional, Dict, List, Sequence, Callable, Iterable, Union
import numpy as np
from collections import defaultdict
import itertools

import matplotlib.pyplot as plt


class Game:
    def __init__(
        self,
        agent0: Agent,
        agent1: Agent,
        state: Optional[State] = None,
        game_size: str = "l",
        logic: Logic = Logic(),
        fixed_setups: Dict[Team, Optional[Iterable[Piece]]] = None,
        seed: Optional[Union[np.random.Generator, int]] = None,
    ):
        self.agents: Dict[Team, Agent] = {
            Team(agent0.team): agent0,
            Team(agent1.team): agent1,
        }
        self.hook_handler: Dict[HookPoint, List[Callable]] = defaultdict(list)
        self._gather_hooks(agents=(agent0, agent1))
        self.fixed_setups: Dict[Team, Optional[Sequence[Piece]]] = dict()
        for team in Team:
            if setup := fixed_setups[team] is not None:
                self.fixed_setups[team] = tuple(setup)
            else:
                self.fixed_setups[team] = None

        self.specs: GameSpecification = GameSpecification(game_size)

        self.logic = logic
        self.state: State
        if state is not None:
            self.state = state
            self.state.dead_pieces = logic.compute_dead_pieces(
                state.board, self.specs.token_count
            )
        else:
            self.reset()

        self.rng_state = np.random.default_rng(seed)

    def __str__(self):
        return np.array_repr(self.state.board)

    def __hash__(self):
        return hash(str(self))

    def _gather_hooks(self, agents: Iterable[Agent]):
        for agent in agents:
            for hook_point, hooks in agent.hooks.items():
                self.hook_handler[hook_point].extend(hooks)

    def reset(self):
        self.state = State(
            Board(self.draw_board()),
            starting_team=self.rng_state.choice([Team.blue, Team.red]),
        )
        return self

    def run_game(self, show=False, **kwargs):
        game_over = False
        block = kwargs.pop("block", False)
        kwargs_print = slice_kwargs(Board.print_board, kwargs)
        kwargs_run_step = slice_kwargs(self.run_step, kwargs)
        if show:
            # if the engine progress should be shown, then we refer to the board print method.
            def print_board():
                self.state.board.print_board(**kwargs_print)
                plt.show(block=block)

        else:
            # if the engine progress should not be shown, then we simply pass over this step.
            def print_board():
                pass

        if (status := self.logic.get_status(self.state)) != Status.ongoing:
            game_over = True

        self._trigger_hooks(HookPoint.pre_run, self.state)

        while not game_over:
            print_board()
            status = self.run_step(**kwargs_run_step)
            if status != Status.ongoing:
                game_over = True
        print_board()

        self._trigger_hooks(HookPoint.post_run, self.state, status)

        return status

    def run_step(self, move: Optional[Move] = None):
        """
        Execute one step of the engine (i.e. the action decided by the active player).

        Parameters
        ----------
        move: Move (optional),
            hijack parameter, if the move should be decided from outside and not the active agent itself.

        Returns
        -------
        Status,
            the current status of the engine.
        """
        player = self.state.active_team
        agent = self.agents[player]

        self._trigger_hooks(HookPoint.pre_move_decision, self.state)

        if move is None:
            move = agent.decide_move(self.state.get_info_state(player))

        self._trigger_hooks(HookPoint.post_move_decision, self.state, move)

        if not self.logic.is_legal_move(self.state.board, move):
            self.reward_agent(agent, RewardToken.illegal)
            return Status.win_red if player == Team.blue else Status.win_blue

        self.state.history.commit_move(
            self.state.board,
            move,
            self.state.turn_counter,
        )

        self._trigger_hooks(HookPoint.pre_move_execution, self.state, move)

        fight_status = self.logic.execute_move(
            self.state, move
        )  # execute agent's choice

        self._trigger_hooks(
            HookPoint.post_move_execution, self.state, move, fight_status
        )

        if fight_status is not None:
            if fight_status == 1:
                self.reward_agent(agent, RewardToken.kill)
            elif fight_status == -1:
                self.reward_agent(agent, RewardToken.die)
            else:
                self.reward_agent(agent, RewardToken.kill_mutually)

        # test if engine is over
        if (status := self.logic.get_status(self.state)) != Status.ongoing:
            return status

        self.state.turn_counter += 1

        return Status.ongoing

    def _trigger_hooks(self, hook_point: HookPoint, *args, **kwargs):
        for hook in self.hook_handler[hook_point]:
            hook(*args, **kwargs)

    def draw_board(self):
        """
        Draw a random board according to the current engine specification.

        Returns
        -------
        np.ndarray,
            the setup, in numpy array form
        """
        rng = self.rng_state
        token_count = self.specs.token_count
        all_tokens = list(token_count.keys())
        token_freqs = list(token_count.values())

        def erase(list_cont, i):
            return list_cont[:i] + list_cont[i + 1 :]

        board = Board(
            np.empty((self.specs.game_size, self.specs.game_size), dtype=object)
        )  # inits all entries to None
        for team in Team:
            if (setup := self.fixed_setups[team]) is not None:
                for piece in setup:
                    board[piece.position] = piece
            else:
                setup_rows = self.specs.setup_rows[team]

                all_pos = [
                    Position(r, c)
                    for r, c in itertools.product(
                        setup_rows, range(self.specs.game_size)
                    )
                ]

                while all_pos:

                    token_draw = rng.choice(
                        np.arange(len(all_tokens)),
                        p=list(map(lambda x: x / sum(token_freqs), token_freqs)),
                    )
                    token = all_tokens[token_draw]
                    version = token_freqs[token_draw]
                    token_freqs[token_draw] -= 1
                    if token_freqs[token_draw] <= 0:
                        # if no such token is left to be drawn, then remove it from the token list
                        erase(all_tokens, token_draw)
                        erase(token_freqs, token_draw)

                    pos_draw = rng.choice(np.arange(len(all_pos)))
                    pos = all_pos[pos_draw]
                    erase(all_pos, all_tokens)

                    board[pos] = Piece(pos, team, token, version)

        for obs_pos in self.specs.obstacle_positions:
            board[obs_pos] = Obstacle(obs_pos)

        return board

    @staticmethod
    def reward_agent(agent: Agent, reward: RewardToken):
        if isinstance(agent, RLAgent):
            agent.add_reward(reward)