##-----------------------------------------------------------------------------
##
## challenge specific code and configuration
##
##-----------------------------------------------------------------------------
import random


## A Synapse project will hold the assetts for your challenge. Put its
## synapse ID here, for example
## CHALLENGE_SYN_ID = "syn1234567"
CHALLENGE_SYN_ID = ""

## Name of your challenge, defaults to the name of the challenge's project
CHALLENGE_NAME = ""

## Synapse user IDs of the challenge admins who will be notified by email
## about errors in the scoring script
ADMIN_USER_IDS = []

## Each question in your challenge should have an evaluation queue through
## which participants can submit their predictions or models. The queues
## should specify the challenge project as their content source. Queues
## can be created like so:
##   evaluation = syn.store(Evaluation(
##     name="My Challenge Q1",
##     description="Predict all the things!",
##     contentSource="syn1234567"))
## ...and found like this:
##   evaluations = list(syn.getEvaluationByContentSource('syn3375314'))
## Configuring them here as a list will save a round-trip to the server
## every time the script starts.
evaluation_queues = []
evaluation_queue_by_id = {q['id']:q for q in evaluation_queues}

## Tables can be created to represent leader boards. Here we're adding
## columns for score, rmse and auc to the basic info for a submission
## which corresponds to the output of our scoring function below.
leaderboard_columns = {}
for q in evaluation_queues:
    leaderboard_columns[q['id']] = [
        {'column_name':'objectId',          'display_name':'ID',     'type':str},
        {'column_name':'userId',            'display_name':'user ID','type':str, 'renderer':'userid'},
        {'column_name':'entityId',          'display_name':'entity', 'type':str, 'renderer':'synapseid'},
        {'column_name':'versionNumber',     'display_name':'version','type':int},
        {'column_name':'name',              'display_name':'name',   'type':str},
        {'column_name':'team',              'display_name':'team',   'type':str},
        {'column_name':'score',             'display_name':'score',  'type':float},
        {'column_name':'rmse',              'display_name':'rmse',   'type':float},
        {'column_name':'auc',               'display_name':'auc',    'type':float}]



def validate_submission(evaluation, submission):
    """
    Find the right validation function and validate the submission.

    :returns: (True, message) if validated, (False, message) if
              validation fails or throws exception
    """
    return True, "Looks OK to me!"


def score_submission(evaluation, submission):
    """
    Find the right scoring function and score the submission

    :returns: (score, message) where score is a dict of stats and message
              is text for display to user
    """
    return (
        dict(score=random.random(), rmse=random.random(), auc=random.random()),
        "Your submission has been scored!")

