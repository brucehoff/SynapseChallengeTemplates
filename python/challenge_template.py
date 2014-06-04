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

CHALLENGE_PROJECT_WIKI = """\
# {title}

Join button to register
${{jointeam?teamId={teamId}&showProfileForm=true&isMemberMessage=You have successfully joined the challenge&text=Join&successMessage=Invitation Accepted}}

|Launch date: ||
|Final Submission date: ||

## Challenge overview
High level summary of the Challenge including the Challenge questions and their significance

## Detailed information
Add these sections as separate wiki pages to give a full description of the challenge:
 * News
 * Data Description
 * Questions and Scoring
 * Submitting Results
 * Leaderboards
 * Computing Resources
 * Challenge Organizers

${{evalsubmit?subchallengeIdList={evalId}&unavailableMessage=Join the team to submit to the challenge}}

## Logos and graphics
 * Challenge Banner
 * DREAM/Sage logos in top left corner
 * Data Contributor institution logos
 * Challenge Funders and Sponsors logos

## Help
Link to forum where all questions about the Challenge should be posted

For more information see [Creating a Challenge Space in Synapse](#!Synapse:syn2453886/wiki/)
"""

LEADERBOARD_MARKDOWN = """\
#Leaderboard

{supertable}

"""

## define the columns that will make up the leaderboard
LEADERBOARD_COLUMNS = [ {'column_name':'objectId', 'display_name':'ID'},
                        {'column_name':'name', 'display_name':'name'},
                        {'column_name':'entityId', 'display_name':'entity', 'renderer':'synapseid'},
                        {'column_name':'status', 'display_name':'status'},
                        {'column_name':'submitterAlias', 'display_name':'team'},
                        {'column_name':'userId', 'display_name':'user ID', 'renderer':'userid'},
                        {'column_name':'bayesian_whatsajigger', 'display_name':'Bayesian Whatsajigger'},
                        {'column_name':'root_mean_squared_flapdoodle', 'display_name':'RMSF'},
                        {'column_name':'discombobulation_index', 'display_name':'Discombobulation', 'sort':'DESC'} ]


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


def create_team(name, description):
    team = {'name': name, 'description': description, 'canPublicJoin':True}
    return syn.restPOST("/team", body=json.dumps(team))


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
    time.sleep(3)
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

        # Create teams for participants and administrators
        participants_team = create_team(CHALLENGE_PROJECT_NAME+' Participants', description='A team for people who have joined the challenge')
        admin_team = create_team(CHALLENGE_PROJECT_NAME+' Administrators', description='A team for challenge administrators')

        syn.setPermissions(challenge_project, admin_team['id'], ['READ', 'UPDATE', 'DELETE', 'CHANGE_PERMISSIONS', 'DOWNLOAD', 'PARTICIPATE', 'SUBMIT'])
        syn.setPermissions(evaluation, participants_team['id'], ['READ', 'PARTICIPATE', 'SUBMIT'])

        # Create the participant project
        participant_project = syn.store(Project(name=name_space_with_user_name(PARTICIPANT_PROJECT_NAME)))
        print "Created project %s %s" % (participant_project.id, participant_project.name)

        participant_file = syn.store(File(synapseclient.utils.make_bogus_data_file(), parent=participant_project))

        return dict(challenge_project=challenge_project,
                    evaluation=evaluation,
                    participant_project=participant_project,
                    participant_file=participant_file,
                    participants_team=participants_team,
                    admin_team=admin_team)

    except Exception as ex:
        tear_down()
        raise


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

    for name in [CHALLENGE_PROJECT_NAME+' Participants', CHALLENGE_PROJECT_NAME+' Administrators']:
        for team in syn._GET_paginated('/teams?fragment=' + urllib.quote_plus(name)):
            print 'deleting', team['id'], team['name']
            syn.restDELETE('/team/{id}'.format(id=team['id']))


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

    ## collect statuses here for batch update
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

        ## we could store each status update individually, but in this example
        ## we collect the updated status objects to do a batch update.
        #status = syn.store(status)
        statuses.append(status)

        sys.stdout.write('.')
        sys.stdout.flush()

    sys.stdout.write('\n')

    ## Update statuses in batch. This can be much faster than individual updates,
    ## especially in rank based scoring methods which recalculate scores for all
    ## submissions each time a new submission is received.
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

            ## annotate each column with it's position in the query results, if it's there
            for column in LEADERBOARD_COLUMNS:
                if column['column_name'] in results.headers:
                    column['index'] = results.headers.index(column['column_name'])

            ## print leaderboard
            print "\t".join([column['display_name'] for column in LEADERBOARD_COLUMNS if 'index' in column])
            for row in results:
                if row[results.headers.index('status')] == 'SCORED':
                    indexes = (column['index'] for column in LEADERBOARD_COLUMNS if 'index' in column)
                    print "\t".join("%0.4f"%row[i] if isinstance(row[i],float) else unicode(row[i]) for i in indexes)

    if not found:
        sys.stderr.write("Error: Annotations have not appeared in query results.\n")


def create_supertable_leaderboard(evaluation):
    """
    Create the leaderboard using a supertable, a markdown extension that dynamically
    builds a table by querying submissions. Because the supertable re-queries whenever
    the page is rendered, this step only has to be done once.
    """
    uri_base = urllib.quote_plus("/evaluation/submission/query")
    # it's incredibly picky that the equals sign here has to be urlencoded, but
    # the later equals signs CAN'T be urlencoded.
    query = urllib.quote_plus('query=select * from evaluation_%s where status=="SCORED"' % utils.id_of(evaluation))
    params = [  ('paging', 'true'),
                ('queryTableResults', 'true'),
                ('showIfLoggedInOnly', 'false'),
                ('pageSize', '25'),
                ('showRowNumber', 'false'),
                ('jsonResultsKeyName', 'rows')]

    # Columns specifications have 4 fields: renderer, display name, column name, sort.
    # Renderer and sort are usually 'none' and 'NONE'.
    for i, column in enumerate(LEADERBOARD_COLUMNS):
        fields = dict(renderer='none', sort='NONE')
        fields.update(column)
        params.append(('columnConfig%s' % i, "{renderer},{display_name},{column_name};,{sort}".format(**fields)))

    return "${supertable?path=" + uri_base + "%3F" + query + "&" + "&".join([key+"="+urllib.quote_plus(value) for key,value in params]) + "}"

    # Notes: supertable fails w/ bizarre error when sorting by a floating point column.
    #        can we format floating point "%0.4f"
    #        supertable is really picky about what gets URL encoded.


def create_wiki(evaluation, challenge_home_entity, team):
    """
    Create landing page for challenge and a sub-page for a leaderboard.

    Note that, while this code demonstrates programmatic generation of wiki markdown
    including leader board table widget, the typical method for creating and editing
    such content is via the Synapse web portal (www.synapse.org).
    """
    wiki = Wiki(
        owner=challenge_home_entity,
        markdown=CHALLENGE_PROJECT_WIKI.format(
            title=CHALLENGE_PROJECT_NAME,
            teamId=team['id'],
            evalId=evaluation.id))
    wiki = syn.store(wiki)

    supertable = create_supertable_leaderboard(evaluation)

    lb_wiki = Wiki(
        owner=challenge_home_entity,
        parentWikiId=wiki.id,
        markdown=LEADERBOARD_MARKDOWN.format(supertable=supertable))
    lb_wiki = syn.store(lb_wiki)




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

        # create leaderboard wiki page
        create_wiki(evaluation, objects['challenge_project'], objects['participants_team'])

    finally:
        if TEAR_DOWN_AFTER:
            tear_down()


if __name__ == '__main__':
    challenge_demo()

