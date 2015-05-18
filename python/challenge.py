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
from synapseclient.annotations import from_submission_status_annotations

from collections import OrderedDict
from datetime import datetime, timedelta
from itertools import izip
from StringIO import StringIO
import copy

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

try:
    import challenge_config as conf
except Exception as ex1:
    sys.stderr.write("\nPlease configure your challenge. See challenge_config.template.py for an example.\n\n")
    raise ex1


# the batch size can be bigger, we do this just to demonstrate batching
BATCH_SIZE = 20

# how many times to we retry batch uploads of submission annotations
BATCH_UPLOAD_RETRY_COUNT = 5

UUID_REGEX = re.compile('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')

# A module level variable to hold the Synapse connection
syn = None


VALIDATION_FAILED_TEMPLATE = """\
Hello {username},

Sorry, but we were unable to validate your submission to the {queue_name}.

Please refer to the challenge instructions which can be found at
https://www.synapse.org/#!Synapse:syn2813558/wiki/209591 and to the error message below:

submission name: {submission_name}
submission ID: {submission_id}

{message}

If you have questions, please ask on the forums at http://support.sagebase.org/sagebase.

Sincerely,

the scoring script
"""

VALIDATION_PASSED_TEMPLATE = """\
Hello {username},

We have received your submission to the {queue_name} and confirmed that it is correctly formatted.

submission name: {submission_name}
submission ID: {submission_id}

Scoring will begin on April 8th and you should receive another message at that time reporting how your submission scored.

If you have questions, please ask on the forums at http://support.sagebase.org/sagebase or refer to the challenge instructions which can be found at https://www.synapse.org/#!Synapse:syn2813558/wiki/209591.

Sincerely,

the scoring script
"""


scoring_message_template = """\
Hello {username},

Your submission \"{submission_name}\" (ID: {submission_id}) to the {queue_name} has been scored:

{message}

If you have questions, please ask on the forums at http://support.sagebase.org/sagebase.

Sincerely,

the scoring script
"""

scoring_error_message_template = """\
Hello {username},

Sorry, but we were unable to process your submission to the {queue_name}.

Please refer to the challenge instructions which can be found at
https://www.synapse.org/#!Synapse:syn2813558/wiki/209591 and to the error message below:

submission name: {submission_name}
submission ID: {submission_id}

{message}

If you have questions, please ask on the forums at http://support.sagebase.org/sagebase.

Sincerely,

the scoring script
"""

error_notification_template = """\
Hello Challenge Administrator,

The scoring script for the """ + conf.CHALLENGE_NAME + """ encountered an error:

{message}

Sincerely,

the scoring script
"""



## define the columns that will make up the leaderboard
LEADERBOARD_COLUMNS = [
    {'column_name':'objectId',          'display_name':'ID',     'type':str},
    {'column_name':'userId',            'display_name':'user ID','type':str, 'renderer':'userid'},
    {'column_name':'entityId',          'display_name':'entity', 'type':str, 'renderer':'synapseid'},
    {'column_name':'versionNumber',     'display_name':'versionNumber','type':int},
    {'column_name':'name',              'display_name':'name',   'type':str},
    {'column_name':'team',              'display_name':'team',   'type':str}]



def get_user_name(profile):
    names = []
    if 'firstName' in profile and profile['firstName'] and profile['firstName'].strip():
        names.append(profile['firstName'])
    if 'lastName' in profile and profile['lastName'] and profile['lastName'].strip():
        names.append(profile['lastName'])
    if len(names)==0:
        names.append(profile['userName'])
    return " ".join(names)


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


def validate(evaluation,
             send_messages=False,
             send_validation_passed_message=False,
             notifications=False,
             dry_run=False):

    if type(evaluation) != Evaluation:
        evaluation = syn.getEvaluation(evaluation)

    print "\n\nValidating", evaluation.id, evaluation.name
    print "-" * 60
    sys.stdout.flush()


    for submission, status in syn.getSubmissionBundles(evaluation, status='RECEIVED'):

        ## refetch the submission so that we get the file path
        ## to be later replaced by a "downloadFiles" flag on getSubmissionBundles
        submission = syn.getSubmission(submission)

        print "validating", submission.id, submission.name
        try:
            is_valid, validation_message = conf.validate_submission(evaluation, submission)
        except Exception as ex1:
            is_valid = False
            validation_message = str(ex1)

        status.status = "VALIDATED" if is_valid else "INVALID"

        if not dry_run:
            status = syn.store(status)

        ## send message AFTER storing status to ensure we don't get repeat messages
        if not is_valid and send_messages:
            profile = syn.getUserProfile(submission.userId)

            message = VALIDATION_FAILED_TEMPLATE.format(
                username=get_user_name(profile),
                queue_name=evaluation.name,
                submission_id=submission.id,
                submission_name=submission.name,
                message=validation_message)
            subject = "Validation error in submission to "+evaluation.name

            if dry_run:
                print "."*30
                print "Dry Run, would have sent:", subject
                print message
            else:
                response = syn.sendMessage(
                    userIds=[submission.userId],
                    messageSubject=subject,
                    messageBody=message)
                #print "sent validation error message: ", unicode(response).encode('utf-8')

        if is_valid and send_validation_passed_message:
            profile = syn.getUserProfile(submission.userId)

            message = VALIDATION_PASSED_TEMPLATE.format(
                username=get_user_name(profile),
                queue_name=evaluation.name,
                submission_id=submission.id,
                submission_name=submission.name)
            subject = "Submission received to "+evaluation.name

            if dry_run:
                print "."*30
                print "Dry Run, would have sent:", subject
                print message
            else:
                response = syn.sendMessage(
                    userIds=[submission.userId],
                    messageSubject=subject,
                    messageBody=message)
                #print "sent validation error message: ", unicode(response).encode('utf-8')


def score(evaluation,
          send_messages=False,
          notifications=False,
          leaderboard_table=None,
          dry_run=False):

    if type(evaluation) != Evaluation:
        evaluation = syn.getEvaluation(evaluation)

    print '\n\nScoring ', evaluation.id, evaluation.name
    print "-" * 60
    sys.stdout.flush()

    ## collect statuses here for batch update
    statuses = []

    for submission, status in syn.getSubmissionBundles(evaluation, status='VALIDATED'):

        status.status = "INVALID"

        ## refetch the submission so that we get the file path
        ## to be later replaced by a "downloadFiles" flag on getSubmissionBundles
        submission = syn.getSubmission(submission)

        try:
            score, message = conf.score_submission(evaluation, submission)

            print "scored:", submission.id, submission.name, submission.userId, score

            ## fill in team in submission status annotations
            if 'teamId' in submission:
                team = syn.restGET('/team/{id}'.format(id=submission.teamId))
                if 'name' in team:
                    score['team'] = team['name']
                else:
                    score['team'] = submission.teamId
            elif 'userId' in submission:
                profile = syn.getUserProfile(submission.userId)
                score['team'] = get_user_name(profile)
            else:
                score['team'] = '?'

            status.annotations = synapseclient.annotations.to_submission_status_annotations(score)
            status.status = "SCORED"

            if leaderboard_table:
                update_leaderboard_table(leaderboard_table, submission, fields=score, dry_run=False)

        except Exception as ex1:
            sys.stderr.write('\n\nError scoring submission %s %s:\n' % (submission.name, submission.id))
            st = StringIO()
            traceback.print_exc(file=st)
            sys.stderr.write(st.getvalue())
            sys.stderr.write('\n')
            message = st.getvalue()

            if notifications and conf.ADMIN_USER_IDS:
                submission_info = "submission id: %s\nsubmission name: %s\nsubmitted by user id: %s\n\n" % (submission.id, submission.name, submission.userId)
                subject = "Exception while scoring" + evaluation.name
                message = error_notification_template.format(message=submission_info+st.getvalue())
                if dry_run:
                    print "."*30
                    print "Dry Run, notification:", subject
                    print message
                else:
                    response = syn.sendMessage(
                        userIds=conf.ADMIN_USER_IDS,
                        messageSubject=subject,
                        messageBody=message)
                    print "sent notification: ", unicode(response).encode('utf-8')

        if not dry_run:
            status = syn.store(status)

        ## send message AFTER storing status to ensure we don't get repeat messages
        if send_messages:
            profile = syn.getUserProfile(submission.userId)

            if status.status == 'SCORED':
                message_body = scoring_message_template.format(
                    message=message,
                    username=get_user_name(profile),
                    queue_name=evaluation.name,
                    submission_name=submission.name,
                    submission_id=submission.id)
                subject = "Submission to "+conf.CHALLENGE_NAME
            else:
                message_body = scoring_error_message_template.format(
                    message=message,
                    username=get_user_name(profile),
                    queue_name=evaluation.name,
                    submission_name=submission.name,
                    submission_id=submission.id)
                subject = "Error scoring submission to "+evaluation.name

            if dry_run:
                print "."*30
                print "Dry Run, would have sent:", subject
                print message_body
            else:
                response = syn.sendMessage(
                    userIds=[submission.userId],
                    messageSubject=subject,
                    messageBody=message_body)
                #print "sent message: ", unicode(response).encode('utf-8')

    sys.stdout.write('\n')


def update_leaderboard_table(leaderboard_table, submission, fields, dry_run=False):
    """
    Insert or update a record in a leaderboard table for a submission.

    :param fields: a dictionary including all scoring statistics plus the team name for the submission.
    """

    ## copy fields from submission
    ## fields should already contain scoring stats
    fields['objectId'] = submission.id
    fields['userId'] = submission.userId
    fields['entityId'] = submission.entityId
    fields['versionNumber'] = submission.versionNumber
    fields['name'] = submission.name

    results = syn.tableQuery("select * from %s where objectId=%s" % (leaderboard_table, submission.id), resultsAs="rowset")
    rowset = results.asRowSet()

    ## figure out if we're inserting or updating
    if len(rowset['rows']) == 0:
        row = {'values':[]}
        rowset['rows'].append(row)
        mode = 'insert'
    elif len(rowset['rows']) == 1:
        row = rowset['rows'][0]
        mode = 'update'
    else:
        ## shouldn't happen
        raise RuntimeError("Multiple entries in leaderboard table %s for submission %s" % (leaderboard_table,submission.id))

    ## build list of fields in proper order according to headers
    row['values'] = [fields[col['name']] for col in rowset['headers']]

    if dry_run:
        print mode, "row "+row['rowId'] if 'rowId' in row else "new row", row['values']
    else:
        return syn.store(rowset)


def query(evaluation, display_columns=LEADERBOARD_COLUMNS, out=sys.stdout):
    """Test the query that will be run to construct the leaderboard"""

    if type(evaluation) != Evaluation:
        evaluation = syn.getEvaluation(evaluation)

    ## Note: Constructing the index on which the query operates is an
    ## asynchronous process, so we may need to wait a bit.
    results = Query(query="select * from evaluation_%s where status==\"SCORED\"" % evaluation.id)

    ## annotate each column with it's position in the query results, if it's there
    cols = copy.deepcopy(display_columns)
    for column in cols:
        if column['column_name'] in results.headers:
            column['index'] = results.headers.index(column['column_name'])
    indices = [column['index'] for column in cols if 'index' in column]
    column_index = {column['index']:column for column in cols if 'index' in column}

    def column_to_string(row, column_index, i):
        if column_index[i]['type']==float:
            return "%0.6f"%float(row[i])
        elif column_index[i]['type']==str:
            return "\"%s\""%unicode(row[i]).encode('utf-8')
        else:
            return unicode(row[i]).encode('utf-8')

    ## print leaderboard
    out.write(",".join([column['display_name'] for column in cols if 'index' in column]) + "\n")
    for row in results:
        out.write(",".join(column_to_string(row, column_index, i) for i in indices))
        out.write("\n")


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


def list_submissions(evaluation, status=None, **kwargs):
    if isinstance(evaluation, basestring):
        evaluation = syn.getEvaluation(evaluation)
    print '\n\nSubmissions for: %s %s' % (evaluation.id, evaluation.name.encode('utf-8'))
    print '-' * 60

    for submission, status in syn.getSubmissionBundles(evaluation, status=status):
        print submission.id, submission.createdOn, status.status, submission.name.encode('utf-8'), submission.userId


def list_evaluations(project):
    print '\n\nEvaluations for project: ', utils.id_of(project)
    print '-' * 60

    evaluations = syn.getEvaluationByContentSource(project)
    for evaluation in evaluations:
        print "Evaluation: %s" % evaluation.id, evaluation.name.encode('utf-8')


## ==================================================
##  Handlers for commands
## ==================================================

def command_list(args):
    """
    List either the submissions to an evaluation queue or
    the evaluation queues associated with a given project.
    """
    if args.all:
        for queue_info in conf.evaluation_queues:
            list_submissions(evaluation=queue_info['id'],
                             status=args.status)
    elif args.challenge_project:
        list_evaluations(project=args.challenge_project)
    elif args.evaluation:
        list_submissions(evaluation=args.evaluation,
                         status=args.status)
    else:
        list_evaluations(project=conf.CHALLENGE_SYN_ID)


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
    if args.rescore_all:
        for queue_info in conf.evaluation_queues:
            for submission, status in syn.getSubmissionBundles(queue_info['id'], status="SCORED"):
                status.status = args.status
                if not args.dry_run:
                    print unicode(syn.store(status)).encode('utf-8')
    for submission in args.submission:
        status = syn.getSubmissionStatus(submission)
        status.status = args.status
        if not args.dry_run:
            print unicode(syn.store(status)).encode('utf-8')


def command_validate(args):
    if args.all:
        for queue_info in conf.evaluation_queues:
            validate(queue_info['id'],
                     send_messages=args.send_messages,
                     send_validation_passed_message=args.send_validation_passed_message,
                     notifications=args.notifications,
                     dry_run=args.dry_run)
    elif args.evaluation:
        validate(args.evaluation,
             send_messages=args.send_messages,
             send_validation_passed_message=args.send_validation_passed_message,
             notifications=args.notifications,
             dry_run=args.dry_run)
    else:
        sys.stderr.write("\nValidate command requires either an evaluation ID or --all to validate all queues in the challenge")


def command_score(args):
    if args.all:
        for queue_info in conf.evaluation_queues:
            score(queue_info['id'],
                  send_messages=args.send_messages,
                  notifications=args.notifications,
                  leaderboard_table=queue_info['leaderboard_table'],
                  dry_run=args.dry_run)
    elif args.evaluation:
        queue_info = conf.evaluation_queue_by_id[args.evaluation]
        score(args.evaluation,
          send_messages=args.send_messages,
          notifications=args.notifications,
          leaderboard_table=queue_info['leaderboard_table'],
          dry_run=args.dry_run)
    else:
        sys.stderr.write("\Score command requires either an evaluation ID or --all to score all queues in the challenge")


def command_rank(args):
    raise NotImplementedError('Implement a ranking function for your challenge')


def command_leaderboard(args):
    ## show columns specific to an evaluation, if available
    if args.evaluation in conf.leaderboard_columns:
        leaderboard_cols = conf.leaderboard_columns[args.evaluation]
    else:
        leaderboard_cols = LEADERBOARD_COLUMNS

    ## write out to file if --out args given
    if args.out is not None:
        with open(args.out, 'w') as f:
            query(args.evaluation, display_columns=leaderboard_cols, out=f)
        print "Wrote leaderboard out to:", args.out
    else:
        query(args.evaluation, display_columns=leaderboard_cols)



## ==================================================
##  main method
## ==================================================

def main():

    if conf.CHALLENGE_SYN_ID == "":
        sys.stderr.write("Please configure your challenge. See sample_challenge.py for an example.")

    global syn

    parser = argparse.ArgumentParser()

    parser.add_argument("-u", "--user", help="UserName", default=None)
    parser.add_argument("-p", "--password", help="Password", default=None)
    parser.add_argument("--notifications", help="Send error notifications to challenge admins", action="store_true", default=False)
    parser.add_argument("--send-messages", help="Send error confirmation and validation errors to participants", action="store_true", default=False)
    parser.add_argument("--dry-run", help="Perform the requested command without updating anything in Synapse", action="store_true", default=False)
    parser.add_argument("--debug", help="Show verbose error output from Synapse API calls", action="store_true", default=False)

    subparsers = parser.add_subparsers(title="subcommand")

    parser_list = subparsers.add_parser('list', help="List submissions to an evaluation or list evaluations")
    parser_list.add_argument("evaluation", metavar="EVALUATION-ID", nargs='?', default=None)
    parser_list.add_argument("--challenge-project", "--challenge", "--project", metavar="SYNAPSE-ID", default=None)
    parser_list.add_argument("-s", "--status", default=None)
    parser_list.add_argument("--all", action="store_true", default=False)
    parser_list.set_defaults(func=command_list)

    parser_status = subparsers.add_parser('status', help="Check the status of a submission")
    parser_status.add_argument("submission")
    parser_status.set_defaults(func=command_check_status)

    parser_reset = subparsers.add_parser('reset', help="Reset a submission to RECEIVED for re-scoring (or set to some other status)")
    parser_reset.add_argument("submission", metavar="SUBMISSION-ID", type=int, nargs='*', help="One or more submission IDs, or omit if using --rescore-all")
    parser_reset.add_argument("-s", "--status", default='RECEIVED')
    parser_reset.add_argument("--rescore-all", action="store_true", default=False)
    parser_reset.set_defaults(func=command_reset)

    parser_validate = subparsers.add_parser('validate', help="Validate all RECEIVED submissions to an evaluation")
    parser_validate.add_argument("evaluation", metavar="EVALUATION-ID", nargs='?', default=None, )
    parser_validate.add_argument("--all", action="store_true", default=False)
    parser_validate.add_argument("--send-validation-passed-message", help="Send confirmation email to participants when a submission passes validation", action="store_true", default=False)
    parser_validate.set_defaults(func=command_validate)

    parser_score = subparsers.add_parser('score', help="Score all VALIDATED submissions to an evaluation")
    parser_score.add_argument("evaluation", metavar="EVALUATION-ID", nargs='?', default=None)
    parser_score.add_argument("--all", action="store_true", default=False)
    parser_score.set_defaults(func=command_score)

    parser_rank = subparsers.add_parser('rank', help="Rank all SCORED submissions to an evaluation")
    parser_rank.add_argument("evaluation", metavar="EVALUATION-ID", default=None)
    parser_rank.set_defaults(func=command_rank)

    parser_leaderboard = subparsers.add_parser('leaderboard', help="Print the leaderboard for an evaluation")
    parser_leaderboard.add_argument("evaluation", metavar="EVALUATION-ID", default=None)
    parser_leaderboard.add_argument("--out", default=None)
    parser_leaderboard.set_defaults(func=command_leaderboard)

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
            if args.dry_run:
                print "Dry Run: error notification:", "Exception while scoring " + conf.CHALLENGE_NAME
                print message
            else:
                response = syn.sendMessage(
                    userIds=conf.ADMIN_USER_IDS,
                    messageSubject="Exception while scoring " + conf.CHALLENGE_NAME,
                    messageBody=message)
                print "sent notification: ", unicode(response).encode('utf-8')

    finally:
        update_lock.release()

    print "\ndone: ", datetime.utcnow().isoformat()
    print "=" * 75, "\n" * 2


if __name__ == '__main__':
    main()

