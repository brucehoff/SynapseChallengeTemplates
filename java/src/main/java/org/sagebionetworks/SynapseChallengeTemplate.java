package org.sagebionetworks;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.Charset;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Properties;
import java.util.Random;
import java.util.Set;
import java.util.concurrent.TimeUnit;

import org.apache.http.entity.ContentType;
import org.json.simple.JSONArray;
import org.json.simple.JSONObject;
import org.json.simple.parser.JSONParser;
import org.json.simple.parser.ParseException;
import org.sagebionetworks.client.SynapseClient;
import org.sagebionetworks.client.SynapseClientImpl;
import org.sagebionetworks.client.SynapseProfileProxy;
import org.sagebionetworks.client.exceptions.SynapseException;
import org.sagebionetworks.evaluation.model.BatchUploadResponse;
import org.sagebionetworks.evaluation.model.Evaluation;
import org.sagebionetworks.evaluation.model.EvaluationStatus;
import org.sagebionetworks.evaluation.model.Submission;
import org.sagebionetworks.evaluation.model.SubmissionBundle;
import org.sagebionetworks.evaluation.model.SubmissionStatus;
import org.sagebionetworks.evaluation.model.SubmissionStatusBatch;
import org.sagebionetworks.evaluation.model.SubmissionStatusEnum;
import org.sagebionetworks.repo.model.ACCESS_TYPE;
import org.sagebionetworks.repo.model.AccessControlList;
import org.sagebionetworks.repo.model.FileEntity;
import org.sagebionetworks.repo.model.PaginatedResults;
import org.sagebionetworks.repo.model.Project;
import org.sagebionetworks.repo.model.ResourceAccess;
import org.sagebionetworks.repo.model.annotation.Annotations;
import org.sagebionetworks.repo.model.annotation.DoubleAnnotation;
import org.sagebionetworks.repo.model.annotation.LongAnnotation;
import org.sagebionetworks.repo.model.annotation.StringAnnotation;
import org.sagebionetworks.repo.model.query.QueryTableResults;
import org.sagebionetworks.repo.model.query.Row;
import org.sagebionetworks.utils.MD5ChecksumHelper;


/**
 * Executable template for Challenge scoring application
 *
 */
public class SynapseChallengeTemplate {
	private static final boolean USE_STAGING = true;
	
	private static final boolean TEAR_DOWN_BEFORE = false;
	private static final String EXISTING_PROJECT_ID = "syn2429057";
	private static final String EXISTING_PARTICIPANT_PROJECT_ID = "syn2429059";
	private static final String EXISTING_EVALUATION_ID = "2429058";

	private static final boolean TEAR_DOWN_AFTER = false;
	
    // the page size can be bigger, we do this just to demonstrate pagination
    private static int PAGE_SIZE = 200; //20
    
    // the batch size can be bigger, we do this just to demonstrate batching
    private static int BATCH_SIZE = 500; // 20
    
    private static int NUM_OF_SUBMISSIONS_TO_CREATE = 1500; //2*PAGE_SIZE+1; // make sure there are multiple batches to handle

	private static final Random random = new Random();
	
	private static final String FILE_CONTENT = "some file content";
	private static final ContentType CONTENT_TYPE = ContentType.create("text/plain", Charset.defaultCharset());
    
	private static Properties properties = null;

    private SynapseClient synapseAdmin;
    private SynapseClient synapseParticipant;
    private Project project;
    private Evaluation evaluation;
    private Project participantProject;
    private FileEntity file;
    

	
    public static void main( String[] args ) throws Exception {
   		SynapseChallengeTemplate sct = new SynapseChallengeTemplate();
   	    try {
   	    	if (TEAR_DOWN_BEFORE) {
   	    		sct.retrieveExisting();
   	    		sct.tearDown();
   	    	}
    		// Set Up
    		sct.setUp();
    	
    		// Create Submission
    		sct.submit();
    	
    		// Scoring application
    		// This is the part of the code that can be used
    		// as a template for actual scoring applications
    		sct.score();
    	
    		// Query for leader board
    		sct.query();
    	} finally {
    		// tear down
    		if (TEAR_DOWN_AFTER) sct.tearDown();
    	}
    }
    
    public SynapseChallengeTemplate() throws SynapseException {
    	synapseAdmin = createSynapseClient();
    	String adminUserName = getProperty("ADMIN_USERNAME");
    	String adminPassword = getProperty("ADMIN_PASSWORD");
    	synapseAdmin.login(adminUserName, adminPassword);
    	synapseParticipant = createSynapseClient();
    	String participantUserName = getProperty("PARTICIPANT_USERNAME");
    	String participantPassword = getProperty("PARTICIPANT_PASSWORD");
    	synapseParticipant.login(participantUserName, participantPassword);
   }
    
    /**
     * Create a project for the Challenge.
     * Create the Evaluation queue.
     * Provide access to the participant.
     */
    public void setUp() throws SynapseException{
    	project = new Project();
    	project.setName("SynapseChallengeTemplate java edition");
    	project = synapseAdmin.createEntity(project);
    	System.out.println("Created "+project.getId()+" "+project.getName());
    	evaluation = new Evaluation();
    	evaluation.setContentSource(project.getId());
    	evaluation.setName("SynapseChallengeTemplate java edition");
    	evaluation.setStatus(EvaluationStatus.OPEN);
    	evaluation = synapseAdmin.createEvaluation(evaluation);
    	AccessControlList acl = synapseAdmin.getEvaluationAcl(evaluation.getId());
    	Set<ResourceAccess> ras = acl.getResourceAccess();
    	ResourceAccess ra = new ResourceAccess();
    	String participantId = synapseParticipant.getMyProfile().getOwnerId();
    	// Note:  Rather than adding a participant directly to the Evaluation's ACL,
    	// We can add a Team (created for the challenge) to the ACL and then add
    	// the participant to that Team
    	ra.setPrincipalId(Long.parseLong(participantId));
    	Set<ACCESS_TYPE> accessTypes = new HashSet<ACCESS_TYPE>();
    	accessTypes.add(ACCESS_TYPE.SUBMIT);
    	accessTypes.add(ACCESS_TYPE.READ);
    	ra.setAccessType(accessTypes);
    	ras.add(ra);
    	synapseAdmin.updateEvaluationAcl(acl);
    	
    	// participant creates their own project
    	participantProject = new Project();
    	participantProject = synapseParticipant.createEntity(participantProject);
    	// participant creates a file which will be their submission
    	file = new FileEntity();
    	String fileHandleId = synapseParticipant.uploadToFileHandle(
    			FILE_CONTENT.getBytes(Charset.defaultCharset()), CONTENT_TYPE);
    	file.setDataFileHandleId(fileHandleId);
    	file.setParentId(participantProject.getId());
    	file = synapseParticipant.createEntity(file);
    	System.out.println("Created participant project: "+participantProject.getId());
   }
    
    /**
     * Submit the file to the Evaluation
     * @throws SynapseException
     */
    public void submit() throws SynapseException {
    	for (int i=0; i<NUM_OF_SUBMISSIONS_TO_CREATE; i++) {
	    	Submission submission = new Submission();
	    	submission.setEntityId(file.getId());
	    	submission.setVersionNumber(file.getVersionNumber());
	    	submission.setEvaluationId(evaluation.getId());
	    	synapseParticipant.createSubmission(submission, file.getEtag());
    	}
    	System.out.println("Submitted "+NUM_OF_SUBMISSIONS_TO_CREATE+" submissions to Evaluation queue: "+evaluation.getId());
    }
    
    /**
     * There are two types of scoring, that in which each submission is scored along and that
     * in which the entire set of submissions is rescored whenever a new one arrives.  This
     * demonstrates the latter
     * @throws SynapseException
     */
    public void score() throws SynapseException, IOException {
    	long startTime = System.currentTimeMillis();
    	List<SubmissionStatus> statusesToUpdate = new ArrayList<SubmissionStatus>();
    	long total = Integer.MAX_VALUE;
       	for (int offset=0; offset<total; offset+=PAGE_SIZE) {
       		PaginatedResults<SubmissionBundle> submissionPGs = 
       				synapseAdmin.getAllSubmissionBundles(evaluation.getId(), offset, PAGE_SIZE);
        	total = (int)submissionPGs.getTotalNumberOfResults();
        	List<SubmissionBundle> page = submissionPGs.getResults();
        	for (int i=0; i<page.size(); i++) {
        		SubmissionBundle bundle = page.get(i);
        		Submission sub = bundle.getSubmission();
        		// at least once, download file and make sure it's correct
        		if (offset==0 && i==0) {
        			String fileHandleId = getFileHandleIdFromEntityBundle(sub.getEntityBundleJSON());
        			File temp = File.createTempFile("temp", null);
        			synapseAdmin.downloadFromSubmission(sub.getId(), fileHandleId, temp);
        			String expectedMD5 = MD5ChecksumHelper.getMD5ChecksumForByteArray(FILE_CONTENT.getBytes(Charset.defaultCharset()));
        			String actualMD5 = MD5ChecksumHelper.getMD5Checksum(temp);
        			if (!expectedMD5.equals(actualMD5)) throw new IllegalStateException("Downloaded file does not have expected content.");
        		}
        		SubmissionStatus status = bundle.getSubmissionStatus();
        		SubmissionStatusEnum currentStatus = status.getStatus();
        		if (currentStatus.equals(SubmissionStatusEnum.SCORED)) {
        			// A scorer can filter out submissions which are already scored, are invalid, etc.
        		}
        		Annotations annotations = status.getAnnotations();
        		if (annotations==null) {
        			annotations=new Annotations();
        			status.setAnnotations(annotations);
        		}
    			addAnnotations(annotations, offset+i+1);
    			status.setStatus(SubmissionStatusEnum.SCORED);
    			statusesToUpdate.add(status);
        	}
       	}
       	
       	System.out.println("Retrieved "+total+" submissions for scoring.");
       	
       	// now we have a batch of statuses to update
       	String batchToken = null;
       	for (int offset=0; offset<statusesToUpdate.size(); offset+=BATCH_SIZE) {
       		SubmissionStatusBatch updateBatch = new SubmissionStatusBatch();
       		List<SubmissionStatus> batch = new ArrayList<SubmissionStatus>();
       		for (int i=0; i<BATCH_SIZE && offset+i<statusesToUpdate.size(); i++) {
       			batch.add(statusesToUpdate.get(offset+i));
       		}
       		updateBatch.setStatuses(batch);
       		boolean isFirstBatch = (offset==0);
       		updateBatch.setIsFirstBatch(isFirstBatch);
       		boolean isLastBatch = (offset+BATCH_SIZE)>=statusesToUpdate.size();
       		updateBatch.setIsLastBatch(isLastBatch);
       		updateBatch.setBatchToken(batchToken);
       		BatchUploadResponse response = 
       				synapseAdmin.updateSubmissionStatusBatch(evaluation.getId(), updateBatch);
       		batchToken = response.getNextUploadToken();
       	}
       	
       	System.out.println("Scored "+statusesToUpdate.size()+" submissions.");
       	long delta = System.currentTimeMillis() - startTime;
       	System.out.println("Elapsed time for running scoring app: "+formatInterval(delta));
    }
    
    private static String getFileHandleIdFromEntityBundle(String s) {
    	try {
	    	JSONParser parser = new JSONParser();
	    	JSONObject bundle = (JSONObject)parser.parse(s);
	    	JSONArray fileHandles = (JSONArray)bundle.get("fileHandles");
	    	for (Object elem : fileHandles) {
	    		JSONObject fileHandle = (JSONObject)elem;
	    		if (!fileHandle.get("concreteType").equals("org.sagebionetworks.repo.model.file.PreviewFileHandle")) {
	    			return (String)fileHandle.get("id");
	    		}
	    	}
	    	throw new IllegalArgumentException("File has no file handle ID");
    	} catch (ParseException e) {
    		throw new RuntimeException(e);
    	}
    }
    
    private static String formatInterval(final long l) {
        final long hr = TimeUnit.MILLISECONDS.toHours(l);
        final long min = TimeUnit.MILLISECONDS.toMinutes(l - TimeUnit.HOURS.toMillis(hr));
        final long sec = TimeUnit.MILLISECONDS.toSeconds(l - TimeUnit.HOURS.toMillis(hr) - TimeUnit.MINUTES.toMillis(min));
        final long ms = TimeUnit.MILLISECONDS.toMillis(l - TimeUnit.HOURS.toMillis(hr) - TimeUnit.MINUTES.toMillis(min) - TimeUnit.SECONDS.toMillis(sec));
        return String.format("%02dh:%02dm:%02d.%03ds", hr, min, sec, ms);
    }
    
    private static void addAnnotations(Annotations a, int i) {
		StringAnnotation sa = new StringAnnotation();
		sa.setIsPrivate(false);
		sa.setKey("aString");
		sa.setValue("xyz"+i);
		List<StringAnnotation> sas = a.getStringAnnos();
		if (sas==null) {
			sas = new ArrayList<StringAnnotation>();
			a.setStringAnnos(sas);
		}
		sas.add(sa);
		DoubleAnnotation da = new DoubleAnnotation();
		da.setIsPrivate(false);
		da.setKey("correlation");
		da.setValue(random.nextDouble());
		List<DoubleAnnotation> das = a.getDoubleAnnos();
		if (das==null) {
			das = new ArrayList<DoubleAnnotation>();
			a.setDoubleAnnos(das);
		}
		das.add(da);
		LongAnnotation la = new LongAnnotation();
		la.setIsPrivate(false);
		la.setKey("rank");
		la.setValue((long)i);
		List<LongAnnotation> las = a.getLongAnnos();
		if (las==null) {
			las = new ArrayList<LongAnnotation>();
			a.setLongAnnos(las);
		}
		las.add(la);   	
    }
    
    private static final long WAIT_FOR_QUERY_ANNOTATIONS_MILLIS = 60000L; // a minute
    
    /**
     * This demonstrates retrieving submission scoring results using the Evaluation query API.
     * In practice the query would be put in an "API SuperTable" widget in a wiki page in the
     * Synapse Portal.  A 
     * 
     * ${supertable?path=%2Fevaluation%2Fsubmission%2Fquery%3Fquery%3Dselect%2B%2A%2Bfrom%2Bevaluation%5F2429058&paging=true&queryTableResults=true&showIfLoggedInOnly=false&pageSize=25&showRowNumber=false&jsonResultsKeyName=rows&columnConfig0=none%2CTeam Name%2CsubmitterAlias%3B%2CNONE&columnConfig1=none%2CSubmitter%2CuserId%3B%2CNONE&columnConfig2=none%2CSubmission Name%2Cname%3B%2CNONE&columnConfig3=none%2CSubmission ID%2CobjectId%3B%2CNONE&columnConfig4=epochdate%2CSubmitted On%2CcreatedOn%3B%2CNONE&columnConfig5=none%2CaString%2CaString%3B%2CNONE&columnConfig6=none%2Crank%2Crank%3B%2CNONE&columnConfig7=none%2Ccorrelation%2Ccorrelation%3B%2CNONE}
     * 
     * @throws SynapseException
     */
    public void query() throws SynapseException, InterruptedException {
    	long startTime = System.currentTimeMillis();
    	while (System.currentTimeMillis()<startTime+WAIT_FOR_QUERY_ANNOTATIONS_MILLIS) {
	    	String query = "select * from evaluation_"+evaluation.getId();
	    	QueryTableResults qtr = synapseParticipant.queryEvaluation(query);
	    	long total = qtr.getTotalNumberOfResults();
	    	
	    	if (total<NUM_OF_SUBMISSIONS_TO_CREATE) {
	    		Thread.sleep(2000L);
	    		continue;
	    	}
	    	
	    	// the annotations have been published.  Let's check the results
	    	List<String> headers = qtr.getHeaders();
	    	System.out.println("Columns available for leader board: "+headers);
	    	List<Row> rows = qtr.getRows();
	    	System.out.println(""+rows.size()+" retrieved.");   
	    	return;
    	}
    	//we reach this line only if we time out
    	System.out.println("Error:  Annotations have not appeared in query results.");
    }
    
    public void retrieveExisting() throws SynapseException {
    	// could retrieve by name so we don't need to know the ID
    	participantProject = (Project)synapseParticipant.getEntityById(EXISTING_PARTICIPANT_PROJECT_ID);
    	evaluation = synapseAdmin.getEvaluation(EXISTING_EVALUATION_ID);
    	project = (Project)synapseAdmin.getEntityById(EXISTING_PROJECT_ID);
    }
    
    public void tearDown() throws SynapseException {
    	if (synapseParticipant!=null) {
    		if (participantProject!=null) {
    			if (participantProject.getId()!=null) synapseParticipant.deleteEntity(participantProject);
    			participantProject=null;
    		}
    	}
    	if (synapseAdmin!=null){ 
	    	if (evaluation!=null) {
	    		synapseAdmin.deleteEvaluation(evaluation.getId());
	    		evaluation=null;
	    	}
	    	if (project!=null) {
	    		synapseAdmin.deleteEntity(project);
	    		project=null;
	    	}
    	}
    }
    
	public static void initProperties() {
		if (properties!=null) return;
		properties = new Properties();
		InputStream is = null;
    	try {
    		is = SynapseChallengeTemplate.class.getClassLoader().getResourceAsStream("global.properties");
    		properties.load(is);
    	} catch (IOException e) {
    		throw new RuntimeException(e);
    	} finally {
    		if (is!=null) try {
    			is.close();
    		} catch (IOException e) {
    			throw new RuntimeException(e);
    		}
    	}
   }
	
	public static String getProperty(String key) {
		initProperties();
		String commandlineOption = System.getProperty(key);
		if (commandlineOption!=null) return commandlineOption;
		String embeddedProperty = properties.getProperty(key);
		if (embeddedProperty!=null) return embeddedProperty;
		// (could also check environment variables)
		throw new RuntimeException("Cannot find value for "+key);
	}	
	  
	private static SynapseClient createSynapseClient() {
		SynapseClientImpl scIntern = new SynapseClientImpl();
		if (USE_STAGING) {
			scIntern.setAuthEndpoint("https://repo-staging.prod.sagebase.org/auth/v1");
			scIntern.setRepositoryEndpoint("https://repo-staging.prod.sagebase.org/repo/v1");
			scIntern.setFileEndpoint("https://repo-staging.prod.sagebase.org/file/v1");
		} else { // prod
			scIntern.setAuthEndpoint("https://repo-prod.prod.sagebase.org/auth/v1");
			scIntern.setRepositoryEndpoint("https://repo-prod.prod.sagebase.org/repo/v1");
			scIntern.setFileEndpoint("https://repo-prod.prod.sagebase.org/file/v1");
		}
		return SynapseProfileProxy.createProfileProxy(scIntern);
  }

}
