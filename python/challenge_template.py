#
# Executable template for Challenge scoring application
#
# To use this script, first install the Synapse Python Client
# http://python-docs.synapse.org/
#
# Log in once using your user name and password
#   import synapseclient
#   syn = synapseclient.Synapse()
#   syn.login(<username>, <password>, rememberMe=True)
#
# Your credentials will be saved after which you may run this script with no credentials.
# 
# Author: chris.bare
#
###############################################################################


import synapseclient
import synapseclient.utils as utils
from synapseclient.exceptions import *
from synapseclient import Activity
from synapseclient import Project, Folder, File
from synapseclient import Evaluation, Submission, SubmissionStatus
from synapseclient import Wiki
import json
import math
import random
import sys
import time
import urllib
import uuid

syn = synapseclient.Synapse()
syn.login()

# if true, then tear down at the end, removing all artifacts
# if false, leave the created objects in place for subsequent use
TEAR_DOWN_AFTER = False

# if 'TEAR_DOWN_AFTER' is set to false, then use unique names for projects and the evaluation:
CHALLENGE_PROJECT_NAME = "SynapseChallengeTemplate Python edition"
CHALLENGE_EVALUATION_NAME = "SynapseChallengeTemplate Python edition"
PARTICIPANT_PROJECT_NAME = "SynapseChallengeTemplate Participant Python edition"

# the page size can be bigger, we do this just to demonstrate pagination
PAGE_SIZE = 20
# the batch size can be bigger, we do this just to demonstrate batching
BATCH_SIZE = 20

# how many times to we retry batch uploads of submission annotations
BATCH_UPLOAD_RETRY_COUNT = 5

# make sure there are multiple batches to handle
NUM_OF_SUBMISSIONS_TO_CREATE = 2*PAGE_SIZE+7

WAIT_FOR_QUERY_ANNOTATIONS_SEC = 30.0

VALIDATION_TEMPLATE = """\
Hello {username},

Sorry, but we were unable to process your submission to the XYZ challenge.
Please refer to the challenge instructions which can be found at
http://foo.com/bar/bat and to the error message below:

submission ID: {submission_id}

{message}

If you have questions, please ask on the forums at http://foo.com/fizz/buzz.

Sincerely,

the scoring script
"""


def name_space_with_user_name(name):
    """Synapse project names have to be unique. This creates a name unique to the current user."""
    return syn.getUserProfile()['userName'] + " " + name


def find_project(name):
    """Helper function to find a project by name"""
    results = syn.query('select id from project where name=="%s"' % name)
    if results and results['totalNumberOfResults'] == 1:
        return results['results'][0]['project.id']


def find_evaluations_for_project(syn_id):
    """Helper function to find evaluation queues associated with an entity"""
    results = syn.restGET('/entity/%s/evaluation' % syn_id)
    for result in results['results']:
        yield result


def update_submissions_status_batch(evaluation, statuses):
    for retry in range(BATCH_UPLOAD_RETRY_COUNT):
        try:
            token = None
            offset = 0
            while offset < len(statuses):
                batch = {"statuses"     : statuses[offset:offset+BATCH_SIZE],
                         "isFirstBatch" : (offset==0),
                         "isLastBatch"  : (offset+BATCH_SIZE>=len(statuses)),
                         "batchToken"   : token}
                response = syn.restPUT("/evaluation/%s/statusBatch" % evaluation.id, json.dumps(batch))
                token = response.get('nextUploadToken', None)
                offset += BATCH_SIZE
        except SynapseHTTPError as err:
            # on 412 ConflictingUpdateException we want to retry
            if err.response.status_code == 412:
                sys.stderr.write('%s, retrying...\n' % err.message)
                time.sleep(2)
            else:
                raise


class Query(object):
    """
    An object that helps with paging through annotation query results.

    Also exposes properties totalNumberOfResults, headers and rows.
    """
    def __init__(self, query, limit=20, offset=0):
        self.query = query
        self.limit = limit
        self.offset = offset
        self.fetch_batch_of_results()

    def fetch_batch_of_results(self):
        uri = "/evaluation/submission/query?query=" + urllib.quote_plus("%s limit %s offset %s" % (self.query, self.limit, self.offset))
        results = syn.restGET(uri)
        self.totalNumberOfResults = results['totalNumberOfResults']
        self.headers = results['headers']
        self.rows = results['rows']
        self.i = 0

    def __iter__(self):
        return self

    def next(self):
        if self.i >= len(self.rows):
            if self.offset >= self.totalNumberOfResults:
                raise StopIteration()
            self.fetch_batch_of_results()
        values = self.rows[self.i]['values']
        self.i += 1
        self.offset += 1
        return values


def set_up():
    tear_down()
    try:

        # Create the Challenge Project
        challenge_project = syn.store(Project(name=name_space_with_user_name(CHALLENGE_PROJECT_NAME)))
        print "Created project %s %s" % (challenge_project.id, challenge_project.name)

        evaluation = syn.store(Evaluation(
            name=challenge_project.name,
            contentSource=challenge_project.id,
            status="OPEN",
            submissionInstructionsMessage="To submit to the XYZ Challenge, send a tab-delimited file as described here: https://...",
            submissionReceiptMessage="Your submission has been received. For further information, consult the leader board at https://..."))
        print "Created Evaluation %s %s" % (evaluation.id, evaluation.name)

        # Create the participant project
        participant_project = syn.store(Project(name=name_space_with_user_name(PARTICIPANT_PROJECT_NAME)))
        print "Created project %s %s" % (participant_project.id, participant_project.name)

        participant_file = syn.store(File(synapseclient.utils.make_bogus_data_file(), parent=participant_project))

        return dict(challenge_project=challenge_project,
                    evaluation=evaluation,
                    participant_project=participant_project,
                    participant_file=participant_file)

    except Exception as ex:
        tear_down()


def tear_down():
    print "Cleanup:"

    project_names = [name_space_with_user_name(CHALLENGE_PROJECT_NAME),
                     name_space_with_user_name(PARTICIPANT_PROJECT_NAME)]

    for name in project_names:
        try:
            syn_id = find_project(name)
            if syn_id:
                for evaluation in find_evaluations_for_project(syn_id):
                    try:
                        print "  deleting", evaluation.get('id', '?')
                        syn.restDELETE('/evaluation/%s' % evaluation['id'])
                    except:
                        sys.stderr.write('Failed to clean up evaluation %s\n' % evaluation.get('id', '?'))

                print "  deleting", syn_id
                syn.delete(syn_id)
        except Exception as ex1:
            print ex1
            sys.stderr.write('Failed to clean up: %s\n' % str(name))


def submit_to_challenge(evaluation, participant_file, n=NUM_OF_SUBMISSIONS_TO_CREATE):
    for i in range(n):
        syn.submit(evaluation=evaluation,
                   entity=participant_file,
                   name="Awesome submission %d" % i,
                   teamName="Team Awesome")


def validate_submission(file_path):
    if random.random() < 0.96:
        return True, "Validated"
    else:
        return False, "This submission was randomly selected to be invalid!"


def validate(evaluation):
    """
    It may be convenient to validate submissions in one pass before scoring
    them, especially if scoring takes a long time.
    """
    for submission, status in syn.getSubmissionBundles(evaluation, status='RECEIVED'):

        ## refetch the submission so that we get the file path
        ## to be later replaced by a "downloadFiles" flag on getSubmissionBundles
        submission = syn.getSubmission(submission)

        is_valid, validation_message = validate_submission(submission.filePath)
        print validation_message
        if is_valid:
            status.status = "VALIDATED"
        else:
            status.status = "INVALID"

        syn.store(status)

        ## send message AFTER storing status to ensure we don't get repeat messages
        if not is_valid:
            profile = syn.getUserProfile(submission.userId)

            message = VALIDATION_TEMPLATE.format(
                username=profile.get('firstName', profile.get('userName', profile['ownerId'])),
                submission_id=submission.id,
                message=validation_message)

            syn.sendMessage(
                userIds=[submission.userId],
                messageSubject="Submission to XYZ Challenge",
                messageBody=message)


def score_submission(file_path):
    """
    Generate some random scoring metrics
    """
    return (random.random(), random.random(), random.random())


def score(evaluation):

    sys.stdout.write('scoring')
    sys.stdout.flush()

    ## unlike the validate method, here we'll update statuses in bulk, just to be different
    statuses = []

    for submission, status in syn.getSubmissionBundles(evaluation, status='VALIDATED'):

        ## refetch the submission so that we get the file path
        ## to be later replaced by a "downloadFiles" flag on getSubmissionBundles
        submission = syn.getSubmission(submission)

        score = score_submission(submission.filePath)

        status.status = "SCORED"
        status.score = math.fsum(score) / len(score)
        status.annotations = synapseclient.annotations.to_submission_status_annotations(
            dict(bayesian_whatsajigger=score[0],
                 root_mean_squared_flapdoodle=score[1],
                 discombobulation_index=score[2]))
        #status = syn.store(status)
        statuses.append(status)

        sys.stdout.write('.')
        sys.stdout.flush()

    sys.stdout.write('\n')

    update_submissions_status_batch(evaluation, statuses)


def query(evaluation):
    """Test the query that will be run to construct the leaderboard"""

    ## Note: Constructing the index on which the query operates is an
    ## asynchronous process, so we may need to wait a bit.

    found = False
    start_time = time.time()
    while not found and (time.time()-start_time < WAIT_FOR_QUERY_ANNOTATIONS_SEC):
        results = Query(query="select * from evaluation_%s" % evaluation.id)
        if results.totalNumberOfResults < NUM_OF_SUBMISSIONS_TO_CREATE or 'bayesian_whatsajigger' not in results.headers:
            time.sleep(2)
        else:
            found = True

            columns = [ {'column_name':'objectId', 'display_name':'ID'},
                        {'column_name':'name', 'display_name':'name'},
                        {'column_name':'entityId', 'display_name':'entity'},
                        {'column_name':'status', 'display_name':'status'},
                        {'column_name':'submitterAlias', 'display_name':'team'},
                        {'column_name':'userId', 'display_name':'user ID'},
                        {'column_name':'bayesian_whatsajigger', 'display_name':'Bayesian Whatsajigger'},
                        {'column_name':'root_mean_squared_flapdoodle', 'display_name':'RMSF'},
                        {'column_name':'discombobulation_index', 'display_name':'Discombobulation'} ]

            ## find the position of the columns in the rows
            for column in columns:
                if column['column_name'] in results.headers:
                    column['index'] = results.headers.index(column['column_name'])

            ## print leaderboard
            print "\t".join([column['display_name'] for column in columns if 'index' in column])
            for row in results:
                if row[results.headers.index('status')] == 'SCORED':
                    indexes = (column['index'] for column in columns if 'index' in column)
                    print "\t".join("%0.4f"%row[i] if isinstance(row[i],float) else unicode(row[i]) for i in indexes)

    if not found:
        sys.stderr.write("Error: Annotations have not appeared in query results.\n")


def challenge_demo():
    try:
        # create a Challenge project, evaluation queue, etc.
        objects=set_up()
        evaluation=objects['evaluation']

        # create a handful of challenge submissions
        submit_to_challenge(evaluation, objects['participant_file'], n=NUM_OF_SUBMISSIONS_TO_CREATE)

        # validate correctness
        # (this can be done at the same time as scoring, below, but we
        # demonstrate doing the two tasks separately)
        validate(evaluation)

        # score the validated submissions
        score(evaluation)

        # query the results (this is the action used by dynamic leader boards
        # viewable in challenge web pages)
        query(evaluation)

    finally:
        if TEAR_DOWN_AFTER:
            tear_down()


challenge_demo()

