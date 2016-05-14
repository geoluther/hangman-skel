# -*- coding: utf-8 -*-`
"""api.py - Create and configure the Game API exposing the resources.
This can also contain game logic. For more complex games it would be wise to
move game logic to another file. Ideally the API will be simple, concerned
primarily with communication to/from the API's users."""

## todos:
## add endpoint to check for full word guess
## check that only a single letter is entered for letter guess
## return win if player guesses all letters correctly


import logging
import endpoints
from protorpc import remote, messages
from google.appengine.api import memcache
from google.appengine.api import taskqueue

from models import User, Game, Score
from models import StringMessage, NewGameForm, GameForm, MakeMoveForm,\
    ScoreForms, GuessWordForm, CancelGameForm, GameForms
from utils import get_by_urlsafe

NEW_GAME_REQUEST = endpoints.ResourceContainer(NewGameForm)

CANCEL_GAME_REQUEST = endpoints.ResourceContainer(CancelGameForm,
    urlsafe_game_key=messages.StringField(1),)

GET_GAME_REQUEST = endpoints.ResourceContainer(
    urlsafe_game_key=messages.StringField(1),)

MAKE_MOVE_REQUEST = endpoints.ResourceContainer(
    MakeMoveForm,
    urlsafe_game_key=messages.StringField(1),)

GUESS_WORD_REQUEST = endpoints.ResourceContainer(
    GuessWordForm,
    urlsafe_game_key=messages.StringField(1),)

USER_REQUEST = endpoints.ResourceContainer(user_name=messages.StringField(1),
                                           email=messages.StringField(2))

MEMCACHE_MOVES_REMAINING = 'MOVES_REMAINING'

## rename guess number to hangman

@endpoints.api(name='hangman', version='v1')
class GuessANumberApi(remote.Service):
    """Game API"""
    # create_user endpoint
    @endpoints.method(request_message=USER_REQUEST,
                      response_message=StringMessage,
                      path='user',
                      name='create_user',
                      http_method='POST')
    def create_user(self, request):
        """Create a User. Requires a unique username"""
        if User.query(User.name == request.user_name).get():
            raise endpoints.ConflictException(
                    'A User with that name already exists!')
        user = User(name=request.user_name, email=request.email)
        user.put()
        return StringMessage(message='User {} created!'.format(
                request.user_name))


    # new_game endpoint
    @endpoints.method(request_message=NEW_GAME_REQUEST,
                      response_message=GameForm,
                      path='game',
                      name='new_game',
                      http_method='POST')
    def new_game(self, request):
        """Creates new game"""
        user = User.query(User.name == request.user_name).get()
        if not user:
            raise endpoints.NotFoundException(
                    'A User with that name does not exist!')
        try:
            game = Game.new_game(user.key, request.min,
                                 request.max, request.attempts)
        except ValueError:
            raise endpoints.BadRequestException('Maximum must be greater '
                                                'than minimum!')

        # Use a task queue to update the average attempts remaining.
        # This operation is not needed to complete the creation of a new game
        # so it is performed out of sequence.
        taskqueue.add(url='/tasks/cache_average_attempts')
        return game.to_form('Good luck playing Hangman!')

    # get_game
    @endpoints.method(request_message=GET_GAME_REQUEST,
                      response_message=GameForm,
                      path='game/{urlsafe_game_key}',
                      name='get_game',
                      http_method='GET')
    def get_game(self, request):
        """Return the current game state."""
        game = get_by_urlsafe(request.urlsafe_game_key, Game)
        if game:
            return game.to_form('Time to make a move!')
        else:
            raise endpoints.NotFoundException('Game not found!')

    # make move, i.e guess letter, todo: ignore case?
    @endpoints.method(request_message=MAKE_MOVE_REQUEST,
                      response_message=GameForm,
                      path='game/make_move/{urlsafe_game_key}',
                      name='make_move',
                      http_method='PUT')
    def make_move(self, request):
        """endpoint to guess a letter"""
        game = get_by_urlsafe(request.urlsafe_game_key, Game)
        if game.game_over:
            return game.to_form('Game already over!')

        game.attempts_remaining -= 1

        #  check guess is one letter only
        if len(request.guess) != 1:
            msg = 'One letter at a time please, guess again.'
            game.put()
            return game.to_form(msg)

        # check if guess is already in history
        if request.guess in game.guess_history:
            msg = 'You already tried that letter, guess again.'
            game.put()
            return game.to_form(msg)

        game.guess_history.append(request.guess)

        if request.guess in game.target:
            msg = 'The word contains your letter!'
            game.update_guess_state(request.guess)
        else:
            msg = 'Letter not in word.'

        if game.target == ''.join(game.guess_state):
            game.end_game(True)
            return game.to_form('You guessed all the letters, you win!')

        if game.attempts_remaining < 1:
            game.end_game(False)
            return game.to_form(msg + ' Game over!')
        else:
            game.put()
            return game.to_form(msg)


    # guess word
    @endpoints.method(request_message=GUESS_WORD_REQUEST,
                      response_message=GameForm,
                      path='game/guess_word/{urlsafe_game_key}',
                      name='guess_word',
                      http_method='PUT')
    def guess_word(self, request):
        """endpoint to guess a word"""
        game = get_by_urlsafe(request.urlsafe_game_key, Game)
        if game.game_over:
            return game.to_form('Game already over!')

        game.attempts_remaining -= 1

        if request.guess in game.guess_history:
            msg = 'You already tried that word, guess again.'
            return game.to_form(msg)

        if request.guess == game.target:
            game.update_guess_state(request.guess)
            game.end_game(True)
            game.put()
            return game.to_form("You guessed the word, you win!")
        else:
            game.update_guess_state(request.guess)
            msg = "That's not the word"

        if game.attempts_remaining < 1:
            game.end_game(False)
            return game.to_form(msg + ' Game over!')
        else:
            game.put()
            return game.to_form(msg)


    @endpoints.method(response_message=ScoreForms,
                      path='scores',
                      name='get_scores',
                      http_method='GET')
    def get_scores(self, request):
        """Return all scores"""
        return ScoreForms(items=[score.to_form() for score in Score.query()])


    @endpoints.method(request_message=USER_REQUEST,
                      response_message=ScoreForms,
                      path='scores/user/{user_name}',
                      name='get_user_scores',
                      http_method='GET')
    def get_user_scores(self, request):
        """Returns all of an individual User's scores"""
        user = User.query(User.name == request.user_name).get()
        if not user:
            raise endpoints.NotFoundException(
                    'A User with that name does not exist!')
        scores = Score.query(Score.user == user.key)
        return ScoreForms(items=[score.to_form() for score in scores])


    @endpoints.method(response_message=StringMessage,
                      path='games/average_attempts',
                      name='get_average_attempts_remaining',
                      http_method='GET')
    def get_average_attempts(self, request):
        """Get the cached average moves remaining"""
        return StringMessage(message=memcache.get(MEMCACHE_MOVES_REMAINING) or '')


## new endpoints

    # get_user_games
    @endpoints.method(request_message=USER_REQUEST,
                      response_message=GameForms,
                      path='games/user/{user_name}',
                      name='get_user_games',
                      http_method='GET')
    def get_user_games(self, request):
        """Returns all of an individual User's active"""
        user = User.query(User.name == request.user_name).get()
        if not user:
            raise endpoints.NotFoundException(
                    'A User with that name does not exist!')
        games = Game.query(Game.user == user.key and Game.game_over == False)
        return GameForms(items=[game.to_form("") for game in games])


    # cancel_game
    @endpoints.method(request_message=CANCEL_GAME_REQUEST,
                      response_message=GameForm,
                      path='game/cancel_game/{urlsafe_game_key}',
                      name='cancel_game',
                      http_method='PUT')
    def cancel_game(self, request):
        """cancel a game in progress"""
        game = get_by_urlsafe(request.urlsafe_game_key, Game)
        if game.game_over:
            return game.to_form("Game already over, can't cancel!")
        else:
          game.cancelled(True)
          game.put()
          return game.to_form("game cancelled")

# get_high_scores
# use get scores and filter by ?

# get_user_rankings

# get_game_history
# query by game game, returns
# history of choices, and messages?
# make game history?: [('move 1', 'msg', 'guess'), ('move 2', msg', 'guess')]


    @staticmethod
    def _cache_average_attempts():
        """Populates memcache with the average moves remaining of Games"""
        games = Game.query(Game.game_over == False).fetch()
        if games:
            count = len(games)
            total_attempts_remaining = sum([game.attempts_remaining
                                        for game in games])
            average = float(total_attempts_remaining)/count
            memcache.set(MEMCACHE_MOVES_REMAINING,
                         'The average moves remaining is {:.2f}'.format(average))



api = endpoints.api_server([GuessANumberApi])
