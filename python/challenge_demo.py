#
# Create assets to demonstrate challenge scoring in Synapse
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


# name for challenge project
CHALLENGE_PROJECT_NAME = "Example Synapse Challenge"
PARTICIPANT_PROJECT_NAME = "Example Challenge Participant Project"

# make sure there are multiple batches to handle
NUM_OF_SUBMISSIONS_TO_CREATE = 5

# A module level variable to hold the Synapse connection
syn = None


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


def create_challenge_object(project, participants_team):
    challenge_json = {'participantTeamId':utils.id_of(participants_team), 'projectId':utils.id_of(project)}
    return DictObject(**syn.restPOST("/challenge", body=json.dumps(challenge_json)))


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

        ## the challenge object associates the challenge project with the
        ## participants team
        create_challenge_object(challenge_project, participants_team)

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


def create_supertable_leaderboard(evaluation, leaderboard_columns):
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
    for i, column in enumerate(leaderboard_columns):
        fields = dict(renderer='none', sort='NONE')
        fields.update(column)
        params.append(('columnConfig%s' % i, "{renderer},{display_name},{column_name};,{sort}".format(**fields)))

    return "${supertable?path=" + uri_base + "%3F" + query + "&" + "&".join([key+"="+urllib.quote_plus(value) for key,value in params]) + "}"

    # Notes: supertable fails w/ bizarre error when sorting by a floating point column.
    #        can we format floating point "%0.4f"
    #        supertable is really picky about what gets URL encoded.


def create_wiki(evaluation, challenge_home_entity, team, leaderboard_columns):
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

    supertable = create_supertable_leaderboard(evaluation, leaderboard_columns)

    lb_wiki = Wiki(
        title="Leaderboard",
        owner=challenge_home_entity,
        parentWikiId=wiki.id,
        markdown=LEADERBOARD_MARKDOWN.format(evaluation_name=evaluation.name, supertable=supertable))
    lb_wiki = syn.store(lb_wiki)

    return (wiki, lb_wiki)


def write_config(challenge_syn_id, challenge_name, admin_user_ids=[], evaluation_queues=[]):
    """
    Fill in the template for a challenge config file with details for the
    newly created demo challenge.
    """
    with open('challenge_config.template.py', 'r') as f:
        txt = f.read()

    txt = re.sub(
        "CHALLENGE_SYN_ID = \"\"",
        "CHALLENGE_SYN_ID = \"%s\"" % challenge_syn_id,
        txt, 1)
    txt = re.sub(
        "CHALLENGE_NAME = \"\"",
        "CHALLENGE_NAME = \"%s\"" % challenge_name,
        txt, 1)
    txt = re.sub(
        r"ADMIN_USER_IDS = \[\]",
        "ADMIN_USER_IDS = [%s]" % (",".join("\"%s\""%str(uid) for uid in admin_user_ids)),
        txt, 1)
    txt = re.sub(
        r"evaluation_queues = \[\]",
        "evaluation_queues = [\n    %s]" % (",\n    ".join(q.__repr__() for q in evaluation_queues)),
        txt, 1)

    with open('challenge_config.py', 'w') as f:
        f.write(txt)


def challenge_demo(number_of_submissions=NUM_OF_SUBMISSIONS_TO_CREATE, cleanup=True):
    try:
        # create a Challenge project, evaluation queue, etc.
        objects=set_up()
        evaluation=objects['evaluation']

        # Write challenge config file, which is just an ordinary python
        # script that can be manually edited later.
        current_user = syn.getUserProfile()
        write_config(
            challenge_syn_id=objects['challenge_project'].id, 
            challenge_name=CHALLENGE_PROJECT_NAME,
            admin_user_ids=[current_user.ownerId],
            evaluation_queues=[evaluation])

        ## import challenge *after* we write the config file
        ## 'cause challenge.py imports the config file
        import challenge

        ## a dirty hack to share the same 
        challenge.syn = syn

        # create leaderboard wiki page
        leaderboard_columns = challenge.conf.leaderboard_columns[evaluation.id]
        create_wiki(evaluation, objects['challenge_project'], objects['participants_team'], leaderboard_columns)

        # create submissions
        submit_to_challenge(evaluation, objects['participant_file'], n=number_of_submissions)

        # validate correctness
        # (this can be done at the same time as scoring, below, but we
        # demonstrate doing the two tasks separately)
        challenge.validate(evaluation)

        # score the validated submissions
        challenge.score(evaluation)

        # query the results (this is the action used by dynamic leader boards
        # viewable in challenge web pages). The process of indexing submission
        # annotations for query is asynchronous. Wait a second to give it a
        # fighting chance of finishing.
        time.sleep(1)
        challenge.query(evaluation, display_columns=leaderboard_columns)

    finally:
        if cleanup and "objects" in locals() and objects:
            tear_down(objects)


def command_demo(args):
    challenge_demo(args.number_of_submissions, args.cleanup)


def command_setup(args):
    set_up()


def command_cleanup(args):
    objs = find_objects(args.uuid)
    print "\nCleaning up:", args.uuid
    for key,obj in objs.iteritems():
        print key,obj['name'],obj['id']
    tear_down(objs, dry_run=args.dry_run)


def main():

    global syn

    parser = argparse.ArgumentParser()

    parser.add_argument("-u", "--user", help="UserName", default=None)
    parser.add_argument("-p", "--password", help="Password", default=None)
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

    parser_setup = subparsers.add_parser('setup', help="create challenge artifacts")
    parser_setup.add_argument("-n", "--number-of-submissions", type=int, default=NUM_OF_SUBMISSIONS_TO_CREATE)
    parser_setup.set_defaults(func=command_setup)

    parser_cleanup = subparsers.add_parser('cleanup', help="delete challenge artifacts")
    parser_cleanup.add_argument("uuid", metavar="UUID", help="UUID of challenge artifacts")
    parser_cleanup.set_defaults(func=command_cleanup)

    args = parser.parse_args()

    print "\n" * 2, "=" * 75
    print datetime.utcnow().isoformat()

    syn = synapseclient.Synapse(debug=args.debug)
    if not args.user:
        args.user = os.environ.get('SYNAPSE_USER', None)
    if not args.password:
        args.password = os.environ.get('SYNAPSE_PASSWORD', None)
    syn.login(email=args.user, password=args.password)
    args.func(args)

    print "\ndone: ", datetime.utcnow().isoformat()
    print "=" * 75, "\n" * 2


if __name__ == '__main__':
    main()

