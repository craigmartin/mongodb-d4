
import os, sys
import random
import time

basedir = os.path.realpath(os.path.dirname(__file__))
sys.path.append(os.path.join(basedir, "../../"))

# mongodb-d4
try:
    from mongodbtestcase import MongoDBTestCase
except ImportError:
    from tests import MongoDBTestCase

from costmodel.state import State
from search import Design
from workload import Session
from util import constants
from inputs.mongodb import MongoSniffConverter

class CostModelComponentTestCase(MongoDBTestCase):
    """
        Base test case for cost model components
    """

    COLLECTION_NAMES = ["squirrels", "girls"]
    NUM_DOCUMENTS = 10000000
    NUM_SESSIONS = 100
    NUM_FIELDS = 6
    NUM_NODES = 8
    NUM_INTERVALS = 10

    def setUp(self):
        MongoDBTestCase.setUp(self)

        # WORKLOAD
        self.workload = [ ]
        timestamp = time.time()
        for i in xrange(CostModelComponentTestCase.NUM_SESSIONS):
            sess = self.metadata_db.Session()
            sess['session_id'] = i
            sess['ip_client'] = "client:%d" % (1234+i)
            sess['ip_server'] = "server:5678"
            sess['start_time'] = timestamp

            for j in xrange(0, len(CostModelComponentTestCase.COLLECTION_NAMES)):
                _id = str(random.random())
                queryId = long((i<<16) + j)
                queryContent = { }
                queryPredicates = { }

                responseContent = {"_id": _id}
                responseId = (queryId<<8)
                for f in xrange(0, CostModelComponentTestCase.NUM_FIELDS):
                    f_name = "field%02d" % f
                    if f % 2 == 0:
                        responseContent[f_name] = random.randint(0, 100)
                        queryContent[f_name] = responseContent[f_name]
                        queryPredicates[f_name] = constants.PRED_TYPE_EQUALITY
                    else:
                        responseContent[f_name] = str(random.randint(1000, 100000))
                        ## FOR

                queryContent = { constants.REPLACE_KEY_DOLLAR_PREFIX + "query": queryContent }
                op = Session.operationFactory()
                op['collection']    = CostModelComponentTestCase.COLLECTION_NAMES[j]
                op['type']          = constants.OP_TYPE_QUERY
                op['query_id']      = queryId
                op['query_content'] = [ queryContent ]
                op['resp_content']  = [ responseContent ]
                op['resp_id']       = responseId
                op['predicates']    = queryPredicates
                op['query_time']    = timestamp
                timestamp += 1
                op['resp_time']    = timestamp
                sess['operations'].append(op)
                ## FOR (ops)
            sess['end_time'] = timestamp
            timestamp += 2
            sess.save()
            self.workload.append(sess)
            ## FOR (sess)

        # Use the MongoSniffConverter to populate our metadata
        converter = MongoSniffConverter(self.metadata_db, self.dataset_db)
        converter.no_mongo_parse = True
        converter.no_mongo_sessionizer = True
        converter.process()
        self.assertEqual(CostModelComponentTestCase.NUM_SESSIONS, self.metadata_db.Session.find().count())

        self.collections = dict([ (c['name'], c) for c in self.metadata_db.Collection.fetch()])
        self.assertEqual(len(CostModelComponentTestCase.COLLECTION_NAMES), len(self.collections))

        # Increase the database size beyond what the converter derived from the workload
        for col_name, col_info in self.collections.iteritems():
            col_info['doc_count'] = CostModelComponentTestCase.NUM_DOCUMENTS
            col_info['avg_doc_size'] = 1024 # bytes
            col_info['max_pages'] = col_info['doc_count'] * col_info['avg_doc_size'] / (4 * 1024)
            col_info.save()
        #            print pformat(col_info)

        self.costModelConfig = {
            'max_memory':     1024, # MB
            'skew_intervals': CostModelComponentTestCase.NUM_INTERVALS,
            'address_size':   64,
            'nodes':          CostModelComponentTestCase.NUM_NODES,
        }

        self.state = State(self.collections, self.workload, self.costModelConfig)
    ## DEF
## CLASS