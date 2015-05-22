Challenge Template for Python
=============================

For those writing Synapse challenge scoring applications in Python, these scripts should serve as a starting point giving working examples of many of the tasks typical to running a challenge on Synapse. [Creating a Challenge Space in Synapse](https://www.synapse.org/#!Synapse:syn2453886) is a step-by-step guide to building out a challenge.

To create an example challenge:

    python challenge_demo.py demo --no-cleanup

This will create a challenge project with an example wiki and an evaluation queue. Several test files are then submitted to the challenge, which are then validated and scored. The demo command also creates a challenge_config.py based on the challenge_config.template.py. You'll need to configure scoring functions and other settings in this file to customize scoring to your own challenge questions.

The challenge.py script has several subcommands that help administrate a challenge. To see all the commands, type:

    python challenge.py -h

To list all submissions to a challenge:

    python challenge.py list [evaluation ID]

All the submissions have been scored at this point. If we wanted to rescore, we could reset the status of a submission:

    python challenge.py reset --status RECEIVED [submission ID]

The script can send several types of messages, which are configured in messages.py. The --send-messages
flag instructs the script to email the submitter when a submission fails validation or gets scored. The
--notifications flag sends error messages to challenge administrators, whose synapse user IDs must be
added to challenge_config.py. Let's validate that submission, with the full suite of messages enabled:

    python challenge.py --send-messages --notifications --acknowledge-receipt validate [evaluation ID]

The script also takes a --dry-run parameter for testing.

    python challenge.py --send-messages --notifications --dry-run score [evaluation ID]

OK, let's score for real:

    python challenge.py --send-messages --notifications --dry-run score [evaluation ID]

Go to the challenge project in Synapse and take a look around. You will find a leaderboard in the wikis and also a Synapse table that mirrors the contents of the leaderboard. The script can output the leaderboard in .csv format:

    python challenge.py leaderboard [evaluation ID]

To delete the example and clean up associated resources:

    python challenge_demo.py cleanup [UUID]


