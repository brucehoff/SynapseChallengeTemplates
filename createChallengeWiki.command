#!/bin/bash

if command -v synapse; then 
    echo "synapseclient exists" 
else 
    echo "synapseclient does not exist"
    echo "Install syanpseclient? (y/n)" 
    read install
    if [ $install = 'y' ]; then 
        echo "Installing synapseclient" 
        sudo easy_install pip
        sudo pip install git+https://github.com/thomasyu888/synapsePythonClient.git@develop
    else 
        echo "You cannot run this program, because the synapseclient is not installed.  Please go here to learn how to install the develop version of synapseclient. (https://github.com/Sage-Bionetworks/synapsePythonClient#install-develop-branch)"
        exit
    fi
fi

echo "import synapseclient" > temp.py
echo "import synapseutils as synu" >> temp.py
echo "import getpass"
echo "import sys" >> temp.py
echo "if __name__ == '__main__':" >> temp.py
echo "  synId = sys.argv[1]" >> temp.py
echo "  try:" >> temp.py
echo "      syn = synapseclient.login()" >> temp.py
echo "  except Exception as e:" >> temp.py
echo "      print('Please provide your synapse username/email and password (You will only be prompted once)')" >> temp.py
echo "      Username = raw_input('Username: ')" >> temp.py
echo "      Password = getpass.getpass()" >> temp.py
echo "      syn = synapseclient.login(email=Username, password=Password,rememberMe=True)" >> temp.py
echo "  synu.copyWiki(syn, 'syn2769515',synId)" >> temp.py
echo "Synapse Challenge Page you want to create: "
read input_variable
python temp.py $input_variable
echo "Created template"
synapse onweb $input_variable