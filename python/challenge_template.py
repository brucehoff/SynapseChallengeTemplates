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
from synapseclient.dict_object import DictObject

from collections import OrderedDict
from datetime import datetime, timedelta
from itertools import izip
from StringIO import StringIO

import argparse
import lock
import json
import math
import os
import random
import re
import sys
import time
import traceback
import urllib
import uuid
import warnings



# use unique names for projects and the evaluation:
CHALLENGE_NAME = "Synapse Challenge Template"
CHALLENGE_PROJECT_NAME = CHALLENGE_NAME
CHALLENGE_EVALUATION_NAME = CHALLENGE_NAME
PARTICIPANT_PROJECT_NAME = CHALLENGE_NAME + " Participant Project"

ADMIN_USER_IDS = [1421212]

# the page size can be bigger, we do this just to demonstrate pagination
PAGE_SIZE = 20
# the batch size can be bigger, we do this just to demonstrate batching
BATCH_SIZE = 20

# how many times to we retry batch uploads of submission annotations
BATCH_UPLOAD_RETRY_COUNT = 5

# make sure there are multiple batches to handle
NUM_OF_SUBMISSIONS_TO_CREATE = 5

WAIT_FOR_QUERY_ANNOTATIONS_SEC = 30.0

UUID_REGEX = re.compile('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')

# A module level variable to hold the Synapse connection
syn = None


VALIDATION_TEMPLATE = """\
Hello {username},

Sorry, but we were unable to validate your submission to the """ + CHALLENGE_NAME + """ challenge.

Please refer to the challenge instructions which can be found at
http://foo.com/bar/bat and to the error message below:

submission name: {submission_name}
submission ID: {submission_id}

{message}

If you have questions, please ask on the forums at http://foo.com/fizz/buzz.

Sincerely,

the scoring script
"""

scoring_message_template = """\
Hello {username},

Your submission "{submission_name}" (ID: {submission_id}) to the """ + CHALLENGE_NAME + """ challenge has been scored:

{message}

If you have questions, please ask on the forums at http://foo.com/fizz/buzz.

Sincerely,

the scoring script
"""

scoring_error_message_template = """\
Hello {username},

Sorry, but we were unable to process your submission to the """ + CHALLENGE_NAME + """ challenge.

Please refer to the challenge instructions which can be found at
http://foo.com/bar/bat and to the error message below:

submission name: {submission_name}
submission ID: {submission_id}

{message}

If you have questions, please ask on the forums at http://foo.com/fizz/buzz.

Sincerely,

the scoring script
"""

error_notification_template = """\
Hello Challenge Administrator,

The scoring script for the """ + CHALLENGE_NAME + """ challenge encountered an error:

{message}

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
Link to [forum](http://support.sagebase.org/sagebase) where all questions about the Challenge should be posted.

For more information see [Creating a Challenge Space in Synapse](#!Synapse:syn2453886/wiki/).

This project was created by code in the Python edition of the [Synapse Challenge Templates](https://github.com/Sage-Bionetworks/SynapseChallengeTemplates).
"""

LEADERBOARD_MARKDOWN = """\
## {evaluation_name}

{supertable}

> A few words to explain our scoring method: it's totally random!
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


def update_submissions_status_batch(evaluation, statuses):
    """
    Update statuses in batch. This can be much faster than individual updates,
    especially in rank based scoring methods which recalculate scores for all
    submissions each time a new submission is received.
    """

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
                # sys.stderr.write('%s, retrying...\n' % err.message)
                time.sleep(2)
            else:
                raise


class Team(DictObject):
    def __init__(self, **kwargs):
        super(Team, self).__init__(kwargs)


def create_team(name, description):
    team = {'name': name, 'description': description, 'canPublicJoin':True}
    return Team(**syn.restPOST("/team", body=json.dumps(team)))


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
    try:

        uuid_suffix = " " + str(uuid.uuid4())

        # Create the Challenge Project
        challenge_project = syn.store(Project(name=CHALLENGE_PROJECT_NAME+uuid_suffix))
        print "Created project %s %s" % (challenge_project.id, challenge_project.name)

        evaluation = syn.store(Evaluation(
            name=challenge_project.name,
            contentSource=challenge_project.id,
            status="OPEN",
            submissionInstructionsMessage="To submit to the XYZ Challenge, send a tab-delimited file as described here: https://...",
            submissionReceiptMessage="Your submission has been received. For further information, consult the leader board at https://..."))
        print "Created Evaluation %s %s" % (evaluation.id, evaluation.name)

        # Create teams for participants and administrators
        participants_team = create_team(CHALLENGE_PROJECT_NAME+uuid_suffix+' Participants', description='A team for people who have joined the challenge')
        print "Created team %s %s" % (participants_team.id, participants_team.name)

        admin_team = create_team(CHALLENGE_PROJECT_NAME+uuid_suffix+' Administrators', description='A team for challenge administrators')
        print "Created team %s %s" % (admin_team.id, admin_team.name)

        # give the teams permissions on challenge artifacts
        # see: http://rest.synapse.org/org/sagebionetworks/repo/model/ACCESS_TYPE.html
        # see: http://rest.synapse.org/org/sagebionetworks/evaluation/model/UserEvaluationPermissions.html
        syn.setPermissions(challenge_project, admin_team.id, ['READ', 'UPDATE', 'DELETE', 'CHANGE_PERMISSIONS', 'DOWNLOAD', 'PARTICIPATE', 'SUBMIT', 'READ_PRIVATE_SUBMISSION'])
        syn.setPermissions(evaluation, participants_team.id, ['READ', 'PARTICIPATE', 'SUBMIT'])

        # Create the participant project
        participant_project = syn.store(Project(name=PARTICIPANT_PROJECT_NAME+uuid_suffix))
        print "Created project %s %s" % (participant_project.id, participant_project.name)

        participant_file = syn.store(File(synapseclient.utils.make_bogus_data_file(), parent=participant_project))

        return dict(challenge_project=challenge_project,
                    evaluation=evaluation,
                    participant_project=participant_project,
                    participant_file=participant_file,
                    participants_team=participants_team,
                    admin_team=admin_team)

    except Exception as ex:
        tear_down(locals())
        raise


def find_objects(uuid):
    """Based on the given UUID (as a string), find demo artifacts"""
    found_objects = {}

    results = list(syn.chunkedQuery('select id from project where project.name == "%s"' % (CHALLENGE_PROJECT_NAME+" "+uuid)))
    if results:
        found_objects['challenge_project'] = syn.get(results[0]['project.id'])

    results = list(syn.chunkedQuery('select id from project where project.name == "%s"' % (PARTICIPANT_PROJECT_NAME+" "+uuid)))
    if results:
        found_objects['participant_project'] = syn.get(results[0]['project.id'])

    response = syn.restGET("/teams?fragment=" + urllib.quote(CHALLENGE_PROJECT_NAME+" "+uuid+" Participants"))
    if len(response['results']) > 0:
        found_objects['participants_team'] = Team(**response['results'][0])
    else:
        warnings.warn("Couldn't find team: %s" % (CHALLENGE_PROJECT_NAME+" "+uuid+" Participants"))

    response = syn.restGET("/teams?fragment=" + urllib.quote(CHALLENGE_PROJECT_NAME+" "+uuid+" Administrators"))
    if len(response['results']) > 0:
        found_objects['admin_team'] = Team(**response['results'][0])
    else:
        warnings.warn("Couldn't find team: %s" % (CHALLENGE_PROJECT_NAME+" "+uuid+" Administrators"))

    return found_objects


def tear_down(objects, dry_run=False):
    print "Cleanup:"

    for project in (objects[key] for key in objects.keys() if key.endswith("_project")):
        try:
            for evaluation in syn.getEvaluationByContentSource(project.id):
                try:
                    print "  deleting evaluation ", evaluation.id
                    if not dry_run:
                        syn.restDELETE('/evaluation/%s' % evaluation.id)
                except:
                    sys.stderr.write('Failed to clean up evaluation %s\n' % evaluation.id)

            print "  deleting", project.id
            if not dry_run:
                syn.delete(project)
        except Exception as ex1:
            print ex1
            sys.stderr.write('Failed to clean up project: %s\n' % str(project))

    for team in (objects[key] for key in objects.keys() if key.endswith("_team")):
        print 'deleting', team['id'], team['name']
        if not dry_run:
            syn.restDELETE('/team/{id}'.format(id=team['id']))


def submit_to_challenge(evaluation, participant_file, n=NUM_OF_SUBMISSIONS_TO_CREATE):
    for i in range(n):
        syn.submit(evaluation=evaluation,
                   entity=participant_file,
                   name="Awesome submission %d" % i,
                   teamName="Team Awesome")


def validate_submission(file_path):
    if random.random() < 0.5:
        return True, "Validated"
    else:
        return False, "This submission was randomly selected to be invalid!"


def validate(evaluation,
             send_messages=False,
             notifications=False,
             dry_run=False):
    """
    It may be convenient to validate submissions in one pass before scoring
    them, especially if scoring takes a long time.
    """
    print "\n\nValidating", utils.id_of(evaluation)
    print "-" * 60
    for submission, status in syn.getSubmissionBundles(evaluation, status='RECEIVED'):

        ## refetch the submission so that we get the file path
        ## to be later replaced by a "downloadFiles" flag on getSubmissionBundles
        submission = syn.getSubmission(submission)

        is_valid, validation_message = validate_submission(submission.filePath)
        print submission.id, validation_message
        if is_valid:
            status.status = "VALIDATED"
        else:
            status.status = "INVALID"

        if not dry_run:
            status = syn.store(status)

        ## send message AFTER storing status to ensure we don't get repeat messages
        if not is_valid and send_messages:
            profile = syn.getUserProfile(submission.userId)

            message = VALIDATION_TEMPLATE.format(
                username=profile.get('firstName', profile.get('userName', profile['ownerId'])),
                submission_id=submission.id,
                submission_name=submission.name,
                message=validation_message)

            response = syn.sendMessage(
                userIds=[submission.userId],
                messageSubject="Error validating Submission to "+CHALLENGE_NAME,
                messageBody=message)
            print "sent validation error message: ", unicode(response).encode('utf-8')

def score_submission(submission, file_path):
    """
    Generate some random scoring metrics
    """
    if submission.name.endswith('3'):
        raise Exception('A fake test exception occured while scoring!')

    score = dict(bayesian_whatsajigger=random.random(),
                 root_mean_squared_flapdoodle=random.random(),
                 discombobulation_index=random.random())

    message="\n".join("    %s = %0.5f"%(k,v) for k,v in score.iteritems())

    return (score, message)


def score(evaluation,
          send_messages=False,
          notifications=False,
          dry_run=False):

    sys.stdout.write('\n\nScoring ' + utils.id_of(evaluation))
    sys.stdout.flush()

    ## collect statuses here for batch update
    statuses = []

    for submission, status in syn.getSubmissionBundles(evaluation, status='VALIDATED'):

        ## refetch the submission so that we get the file path
        ## to be later replaced by a "downloadFiles" flag on getSubmissionBundles
        submission = syn.getSubmission(submission)

        try:
            score, message = score_submission(submission, submission.filePath)

            status.status = "SCORED"
            status.score = math.fsum(v for k,v in score.iteritems()) / len(score)
            status.annotations = synapseclient.annotations.to_submission_status_annotations(score)

        except Exception as ex1:
            sys.stderr.write('\n\nError scoring submission %s %s:\n' % (submission.name, submission.id))
            st = StringIO()
            traceback.print_exc(file=st)
            sys.stderr.write(st.getvalue())
            sys.stderr.write('\n')
            status.status = "INVALID"
            message = st.getvalue()

            if notifications and ADMIN_USER_IDS:
                submission_info = "submission id: %s\nsubmission name: %s\nsubmitted by user id: %s\n\n" % (submission.id, submission.name, submission.userId)
                response = syn.sendMessage(
                    userIds=ADMIN_USER_IDS,
                    messageSubject=CHALLENGE_NAME+": exception during scoring",
                    messageBody=error_notification_template.format(message=submission_info+st.getvalue()))
                print "sent notification: ", unicode(response).encode('utf-8')

        if not dry_run:
            status = syn.store(status)

        ## send message AFTER storing status to ensure we don't get repeat messages
        if send_messages:
            profile = syn.getUserProfile(submission.userId)

            if status.status == 'SCORED':
                message_body = scoring_message_template.format(
                    message=message,
                    username=profile.get('firstName', profile.get('userName', profile['ownerId'])),
                    submission_name=submission.name,
                    submission_id=submission.id)
                subject = "Submission to "+CHALLENGE_NAME
            else:
                message_body = scoring_error_message_template.format(
                    message=message,
                    username=profile.get('firstName', profile.get('userName', profile['ownerId'])),
                    submission_name=submission.name,
                    submission_id=submission.id)
                subject = "Error scoring submission to "+CHALLENGE_NAME

            response = syn.sendMessage(
                userIds=[submission.userId],
                messageSubject=subject,
                messageBody=message_body)
            print "sent message: ", unicode(response).encode('utf-8')

        sys.stdout.write('.')
        sys.stdout.flush()

    sys.stdout.write('\n')


def query(evaluation, expected_result_count=NUM_OF_SUBMISSIONS_TO_CREATE):
    """Test the query that will be run to construct the leaderboard"""

    ## Note: Constructing the index on which the query operates is an
    ## asynchronous process, so we may need to wait a bit.
    found = False
    start_time = time.time()
    time.sleep(1)
    while not found and (time.time()-start_time < WAIT_FOR_QUERY_ANNOTATIONS_SEC):
        results = Query(query="select * from evaluation_%s" % evaluation.id)

        if results.totalNumberOfResults < expected_result_count:
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
        title="Leaderboard",
        owner=challenge_home_entity,
        parentWikiId=wiki.id,
        markdown=LEADERBOARD_MARKDOWN.format(evaluation_name=evaluation.name, supertable=supertable))
    lb_wiki = syn.store(lb_wiki)

    return (wiki, lb_wiki)


def list_submissions(evaluation, status=None, **kwargs):
    if isinstance(evaluation, basestring):
        evaluation = syn.getEvaluation(evaluation)
    print '\n\nSubmissions for: %s %s' % (evaluation.id, evaluation.name)
    print '-' * 60

    for submission, status in syn.getSubmissionBundles(evaluation, status=status):
        print submission.id, submission.createdOn, status.status, submission.name.encode('utf-8'), submission.userId


def list_evaluations(project):
    print '\n\nEvaluations for project: ', utils.id_of(project)
    print '-' * 60

    evaluations = syn.getEvaluationByContentSource(project)
    for evaluation in evaluations:
        print "Evaluation: %s" % evaluation.id, evaluation.name.encode('utf-8')


def challenge_demo(number_of_submissions=NUM_OF_SUBMISSIONS_TO_CREATE, cleanup=True):
    try:
        # create a Challenge project, evaluation queue, etc.
        objects=set_up()
        evaluation=objects['evaluation']

        # create submissions
        submit_to_challenge(evaluation, objects['participant_file'], n=number_of_submissions)

        # validate correctness
        # (this can be done at the same time as scoring, below, but we
        # demonstrate doing the two tasks separately)
        validate(evaluation)

        # score the validated submissions
        score(evaluation)

        # query the results (this is the action used by dynamic leader boards
        # viewable in challenge web pages)
        query(evaluation, expected_result_count=number_of_submissions)

        # create leaderboard wiki page
        create_wiki(evaluation, objects['challenge_project'], objects['participants_team'])

    finally:
        if cleanup and "objects" in locals() and objects:
            tear_down(objects)


def command_demo(args):
    challenge_demo(args.number_of_submissions, args.cleanup)


def command_cleanup(args):
    objs = find_objects(args.uuid)
    print "Cleaning up:", args.uuid
    for key,obj in objs.iteritems():
        print key,obj['name'],obj['id']
    tear_down(objs, dry_run=args.dry_run)


def command_list(args):
    if args.challenge_project:
        list_evaluations(project=args.challenge_project)
    elif args.evaluation:
        list_submissions(evaluation=args.evaluation,
                         status=args.status)
    else:
        sys.stderr.write('\nList command requires either an evaluation ID or a synapse project. '\
                         'The list command might also be customized to list evaluations specific '\
                         'to a given challenge.\n')


def command_check_status(args):
    submission = syn.getSubmission(args.submission)
    status = syn.getSubmissionStatus(args.submission)
    evaluation = syn.getEvaluation(submission.evaluationId)
    ## deleting the entity key is a hack to work around a bug which prevents
    ## us from printing a submission
    del submission['entity']
    print unicode(evaluation).encode('utf-8')
    print unicode(submission).encode('utf-8')
    print unicode(status).encode('utf-8')


def command_reset(args):
    for submission in args.submission:
        status = syn.getSubmissionStatus(submission)
        status.status = args.status
        if not args.dry_run:
            print unicode(syn.store(status)).encode('utf-8')


def command_validate(args):
    validate(args.evaluation,
             send_messages=args.send_messages,
             notifications=args.notifications,
             dry_run=args.dry_run)


def command_score(args):
    score(args.evaluation,
          send_messages=args.send_messages,
          notifications=args.notifications,
          dry_run=args.dry_run)


def command_rank(args):
    raise NotImplementedError('Implement a ranking function for your challenge')


def main():

    global syn

    parser = argparse.ArgumentParser()

    parser.add_argument("-u", "--user", help="UserName", default=None)
    parser.add_argument("-p", "--password", help="Password", default=None)
    parser.add_argument("--notifications", help="Send error notifications to challenge admins", action="store_true", default=False)
    parser.add_argument("--send-messages", help="Send error confirmation and validation errors to participants", action="store_true", default=False)
    parser.add_argument("--dry-run", help="Perform the requested command without updating anything in Synapse", action="store_true", default=False)
    parser.add_argument("--debug", help="Show verbose error output from Synapse API calls", action="store_true", default=False)

    subparsers = parser.add_subparsers(title="subcommand")

    parser_demo = subparsers.add_parser('demo', help="Create a test challenge and populate it with some fake submissions")
    parser_demo.add_argument("-n", "--number-of-submissions", type=int, default=NUM_OF_SUBMISSIONS_TO_CREATE)
    group = parser_demo.add_mutually_exclusive_group(required=False)
    group.add_argument("--cleanup", dest='cleanup', action='store_true', help="Delete any Synapse assets created during the demo")
    group.add_argument("--no-cleanup", dest='cleanup', action='store_false')
    parser_demo.set_defaults(cleanup=True)
    parser_demo.set_defaults(func=command_demo)

    parser_cleanup = subparsers.add_parser('cleanup', help="delete challenge artifacts")
    parser_cleanup.add_argument("uuid", metavar="UUID", help="UUID of challenge artifacts")
    parser_cleanup.set_defaults(func=command_cleanup)

    parser_list = subparsers.add_parser('list', help="List submissions to an evaluation or list evaluations")
    parser_list.add_argument("evaluation", metavar="EVALUATION-ID", nargs='?', default=None)
    parser_list.add_argument("--challenge-project", "--challenge", "--project", metavar="SYNAPSE-ID", default=None)
    parser_list.add_argument("-s", "--status", default=None)
    parser_list.set_defaults(func=command_list)

    parser_status = subparsers.add_parser('status', help="Check the status of a submission")
    parser_status.add_argument("submission")
    parser_status.set_defaults(func=command_check_status)

    parser_reset = subparsers.add_parser('reset', help="Reset a submission to RECEIVED for re-scoring (or set to some other status)")
    parser_reset.add_argument("submission", metavar="SUBMISSION-ID", type=int, nargs='+', help="One or more submission IDs")
    parser_reset.add_argument("-s", "--status", default='RECEIVED')
    parser_reset.set_defaults(func=command_reset)

    parser_validate = subparsers.add_parser('validate', help="Validate all RECEIVED submissions to an evaluation")
    parser_validate.add_argument("evaluation", metavar="EVALUATION-ID", default=None)
    parser_validate.set_defaults(func=command_validate)

    parser_score = subparsers.add_parser('score', help="Score all VALIDATED submissions to an evaluation")
    parser_score.add_argument("evaluation", metavar="EVALUATION-ID", default=None)
    parser_score.set_defaults(func=command_score)

    parser_rank = subparsers.add_parser('rank', help="Rank all SCORED submissions to an evaluation")
    parser_rank.add_argument("evaluation", metavar="EVALUATION-ID", default=None)
    parser_rank.set_defaults(func=command_rank)

    args = parser.parse_args()

    print "\n" * 2, "=" * 75
    print datetime.utcnow().isoformat()

    ## Acquire lock, don't run two scoring scripts at once
    try:
        update_lock = lock.acquire_lock_or_fail('challenge', max_age=timedelta(hours=4))
    except lock.LockedException:
        print u"Is the scoring script already running? Can't acquire lock."
        # can't acquire lock, so return error code 75 which is a
        # temporary error according to /usr/include/sysexits.h
        return 75

    try:
        syn = synapseclient.Synapse(debug=args.debug)
        if not args.user:
            args.user = os.environ.get('SYNAPSE_USER', None)
        if not args.password:
            args.password = os.environ.get('SYNAPSE_PASSWORD', None)
        syn.login(email=args.user, password=args.password)
        args.func(args)

    except Exception as ex1:
        sys.stderr.write('Error in scoring script:\n')
        st = StringIO()
        traceback.print_exc(file=st)
        sys.stderr.write(st.getvalue())
        sys.stderr.write('\n')

        if args.notifications:
            message = error_notification_template.format(message=st.getvalue())
            response = syn.sendMessage(
                userIds=ADMIN_USER_IDS,
                messageSubject="Exception while scoring " + CHALLENGE_NAME,
                messageBody=message)
            print "sent notification: ", unicode(response).encode('utf-8')

    finally:
        update_lock.release()

    print "\ndone: ", datetime.utcnow().isoformat()
    print "=" * 75, "\n" * 2


if __name__ == '__main__':
    main()

