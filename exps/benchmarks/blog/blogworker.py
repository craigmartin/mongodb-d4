# -*- coding: iso-8859-1 -*-
# -----------------------------------------------------------------------
# Copyright (C) 2012 by Brown University
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT
# IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
# OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
# ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.
# -----------------------------------------------------------------------

import sys
import os
import string
import re
import logging
import traceback
import pymongo
from datetime import datetime
from pprint import pprint, pformat
import time
import constants
from util import *
from api.abstractworker import AbstractWorker
from api.message import *

LOG = logging.getLogger(__name__)

# BLOGWORKER
# Andy Pavlo - http://www.cs.brown.edu/~pavlo/
# 
# This is the worker for the 'blog' microbenchmark in the paper
# There are three types of experiments that we want to perform on the 
# data generated by this code. These experiments are designed to higlight
# different aspects of database design in MongoDB by demonstrated the
# performance trade-offs of getting it wrong.
# For each experiment type, there are two variations of the workload. The first 
# of which is the "correct" design choice and the second is the "bad" design
# choice. Yes, this is somewhat a simplistic view, but it's mostly 
# meant to be an demonstration rather than a deep analysis of the issues:
#
# Experiment #1: SHARDING KEYS
# For this experiment, we will shard articles by their autoinc id and then 
# by their id+timestamp. This will show that sharding on just the id won't
# work because of skew, but by adding the timestamp the documents are spread out
# more evenly.
# 
# Experiment #2: DENORMALIZATION
# In our microbenchmark we should have a collection of articles and collection of 
# article comments. The target workload will be to grab an article and grab the 
# top 10 comments for that article sorted by a user rating. In the first experiment,
# we will store the articles and comments in separate collections.
# In the second experiment, we'll embedded the comments inside of the articles.
# 
# Experiment #3: INDEXES
# In our final benchmark, we compared the performance difference between a query on 
# a collection with (1) no index for the query's predicate, (2) an index with only one 
# key from the query's predicate, and (3) a covering index that has all of the keys 
# referenced by that query.
# 
class BlogWorker(AbstractWorker):
  
    
    
    def initImpl(self, config, msg):
        #self.opCount=0;
        # A list of booleans that we will randomly select
        # from to tell us whether our op should be a read or write
        self.workloadWrite = [ ]
        for i in xrange(0, constants.WORKLOAD_READ_PERCENT):
            self.workloadWrite.append(False)
        for i in xrange(0, constants.WORKLOAD_WRITE_PERCENT):
            self.workloadWrite.append(True)
        
        # Total number of articles in database
        #the number of articles (e.g 10000) or 10000 x scalefactor is not the real number, because if we have 16 workers, 
        #the number of articles is not equaly divisible by the number of workers, and will be lower than that.
        self.clientprocs = int(self.config["default"]["clientprocs"])
        self.num_articles = int(int(self.getScaleFactor() * constants.NUM_ARTICLES) / self.clientprocs) * self.clientprocs
        self.firstArticle = msg[0]
        self.lastArticle = msg[1]
        self.lastCommentId = None
        self.config[self.name]["commentsperarticle"]
        self.articleZipf = ZipfGenerator(self.num_articles, 1.001)
        LOG.info("Worker #%d Articles: [%d, %d]" % (self.getWorkerId(), self.firstArticle, self.lastArticle))
        numComments = int(config[self.name]["commentsperarticle"])
        
        # Zipfian distribution on the number of comments & their ratings
        self.commentsZipf = ZipfGenerator(numComments, 1.001)
        self.ratingZipf = ZipfGenerator(constants.MAX_COMMENT_RATING+1, 1.001)
        self.db = self.conn[config['default']["dbname"]]   
        
        #precalcualtiong the authors names list to use Zipfian against them
        self.authors = [ ]
        for i in xrange(0, constants.NUM_AUTHORS):
            #authorSize = constants.AUTHOR_NAME_SIZE
            if config[self.name]["experiment"] == constants.EXP_INDEXING:
                self.authors.append("authorname0000000000000000000000000000000000000000000000000000000000000000000"+str(i))
            else:
                self.authors.append("authorname"+str(i))
        self.authorZipf = ZipfGenerator(constants.NUM_AUTHORS,1.001)
        
        #precalculating tags
        self.tags = [ ]
        for i in xrange(0, constants.NUM_TAGS):
            #authorSize = constants.AUTHOR_NAME_SIZE
            if config[self.name]["experiment"] == constants.EXP_INDEXING:
                self.tags.append("tag00000000000000000000000000000000000000000000000000000000000000000000000000000"+str(i))
            else:    
                self.tags.append("tag"+str(i))
        self.tagZipf = ZipfGenerator(constants.NUM_TAGS,1.001)
        
        #precalcualtiong the dates list to use Zipfian against them
        self.dates = [ ]
        #dates in reverse order as we want to have the most recent to be more "accessed" by Zipfian
        self.datecount=0
        epochToStartInSeconds = int(time.mktime(constants.START_DATE.timetuple()))
        epochToStopInSeconds = int(time.mktime(constants.STOP_DATE.timetuple()))
        # 1day = 24*60*60sec = 86400
        for i in xrange(epochToStopInSeconds,epochToStartInSeconds,-86400):
            self.dates.append(datetime.fromtimestamp(i))
            self.datecount +=1
        self.dateZipf = ZipfGenerator(self.datecount,1.001)
        
        
        
        if self.getWorkerId() == 0:
            if config['default']["reset"]:
                LOG.info("Resetting database '%s'" % config['default']["dbname"])
                self.conn.drop_database(config['default']["dbname"])
            
            ## SHARDING
            if config[self.name]["experiment"] == constants.EXP_SHARDING:
                self.enableSharding(config)
        ## IF
        
        #self.initNextCommentId(config[self.name]["maxCommentId"])
    ## DEF
    
    def enableSharding(self, config):
        assert self.db != None
        
        # Enable sharding on the entire database
        try:
            result = self.db.command({"enablesharding": self.db.name})
            assert result["ok"] == 1, "DB Result: %s" % pformat(result)
        except:
            LOG.error("Failed to enable sharding on database '%s'" % self.db.name)
            raise
        
        # Generate sharding key patterns
        # CollectionName -> Pattern
        # http://www.mongodb.org/display/DOCS/Configuring+Sharding#ConfiguringSharding-ShardingaCollection
        shardingPatterns = { }
        
        if config[self.name]["sharding"] == constants.SHARDEXP_SINGLE:
            shardingPattern = {articles : { id : 1}}
        
        elif config[self.name]["sharding"] == constants.SHARDEXP_COMPOUND:
            shardingPattern = {articles : {id : 1, slug : 1}}
        
        else:
            raise Exception("Unexpected sharding configuration type '%d'" % config["sharding"])
        
        # Then enable sharding on each of these collections
        for col,pattern in shardingPatterns.iteritems():
            LOG.debug("Sharding Collection %s.%s: %s" % (self.db.name, col, pattern))
            try:
                result = self.db.command({"shardcollection": col, "key": pattern})
                assert result["ok"] == 1, "DB Result: %s" % pformat(result)
            except:
                LOG.error("Failed to enable sharding on collection '%s.%s'" % (self.db.name, col))
                raise
        ## FOR
        
        LOG.debug("Successfully enabled sharding on %d collections in database %s" % \
                  (len(Patterns, self.db.name)))
    ## DEF
 
    ## ---------------------------------------------------------------------------
    ## STATUS
    ## ---------------------------------------------------------------------------
    
    def statusImpl(self, config, channel, msg):
        result = { }
        for col in self.db.collection_names():
            stats = self.db.validate_collection(col)
            result[self.db.name + "." + col] = (stats.datasize, stats.nrecords)
        ## FOR
        return (result)
    ## DEF
 
    ## ---------------------------------------------------------------------------
    ## LOAD
    ## ---------------------------------------------------------------------------
    
    def loadImpl(self, config, channel, msg):
        assert self.conn != None
        
        # HACK: Setup the indexes if we're the first client
        if self.getWorkerId() == 0:
            self.db[constants.ARTICLE_COLL].drop_indexes()
            self.db[constants.COMMENT_COLL].drop_indexes()
            
            
            ## INDEXES CONFIGURATION
            
            
            if config[self.name]["experiment"] == constants.EXP_INDEXING:
                
                LOG.info("Creating primary key indexes for %s" % self.db[constants.ARTICLE_COLL].full_name)
                self.db[constants.ARTICLE_COLL].ensure_index([("id", pymongo.ASCENDING)])
                       
                LOG.info("Creating index on (author) for %s" % self.db[constants.ARTICLE_COLL].full_name) 
                self.db[constants.ARTICLE_COLL].ensure_index([("author", pymongo.ASCENDING)])
                
                trial = int(config[self.name]["indexes"])
                if trial == 1:
                    LOG.info("Creating index on (author) for %s" % self.db[constants.ARTICLE_COLL].full_name) 
                    self.db[constants.ARTICLE_COLL].ensure_index([("author", pymongo.ASCENDING), \
                                                                  ("tags",pymongo.ASCENDING)])
                
                
                #LOG.info("Creating primary key indexes for %s" % self.db[constants.ARTICLE_COLL].full_name) 
                #self.db[constants.ARTICLE_COLL].ensure_index([("id", pymongo.ASCENDING)])
                
                #LOG.info("Creating indexes (author,date) for %s" % self.db[constants.ARTICLE_COLL].full_name) 
                #self.db[constants.ARTICLE_COLL].ensure_index([("author", pymongo.ASCENDING), \
                 #                                             ("date", pymongo.ASCENDING)])
                
                #LOG.info("Creating indexes (date) for %s" % self.db[constants.ARTICLE_COLL].full_name) 
                #self.db[constants.ARTICLE_COLL].ensure_index([("date", pymongo.ASCENDING)])
            
            elif config[self.name]["experiment"] == constants.EXP_DENORMALIZATION:
                LOG.info("Creating primary key indexes for %s" % self.db[constants.ARTICLE_COLL].full_name) 
                self.db[constants.ARTICLE_COLL].ensure_index([("id", pymongo.ASCENDING)])
                
                if config[self.name]["denormalize"]:
                    LOG.info("Creating indexes (articleId,rating) %s" % self.db[constants.COMMENT_COLL].full_name)
                    self.db[constants.COMMENT_COLL].ensure_index([("article", pymongo.ASCENDING), \
                                                                  ("rating", pymongo.DESCENDING)])
                    
            elif config[self.name]["experiment"] == constants.EXPS_SHARDING:
                #NOTE: we don't need an index on articleId only as we have this composite index -> (articleId,articleSlug)
                LOG.info("Creating indexes (id,slug) %s" % self.db[constants.ARTICLE_COLL].full_name)
                self.db[constants.ARTICLE_COLL].ensure_index([("id", pymongo.ASCENDING), \
                                                              ("slug", pymongo.ASCENDING)])
                
            
            else:
                raise Exception("Unexpected experiment type %s" % config[self.name]["experiment"])
                   
        ## IF
        
        ## ----------------------------------------------
        ## LOAD ARTICLES
        ## ----------------------------------------------
        articleCtr = 0
        articleTotal = self.lastArticle - self.firstArticle
        commentCtr = 0
        commentTotal= 0
        numComments = int(config[self.name]["commentsperarticle"])
        for articleId in xrange(self.firstArticle, self.lastArticle+1):
            titleSize = constants.ARTICLE_TITLE_SIZE
            title = randomString(titleSize)
            contentSize = constants.ARTICLE_CONTENT_SIZE
            content = randomString(contentSize)
            #slug = list(title.replace(" ", ""))
            #if len(slug) > 64: slug = slug[:64]
            #for idx in xrange(0, len(slug)):
            #    if random.randint(0, 10) == 0:
            #       slug[idx] = "-"
            ## FOR
            #slug = "".join(slug)
            articleTags = []
            for ii in xrange(0,constants.NUM_TAGS_PER_ARTICLE):
                 articleTags.append(random.choice(self.tags))
            
            articleDate = randomDate(constants.START_DATE, constants.STOP_DATE)
            articleSlug = '%064d' % hash(str(articleId))
            article = {
                "id": articleId,
                "title": title,
                "date": articleDate,
                "author": random.choice(self.authors),
                "slug" : articleSlug,
                "content": content,
                "numComments": numComments,
                "tags": articleTags,
                "views": 0,
            }
            articleCtr+=1;
            if config[self.name]["denormalize"]:
                article["comments"] = [ ]
            self.db[constants.ARTICLE_COLL].insert(article)
            
            
            ## ----------------------------------------------
            ## LOAD COMMENTS
            ## ----------------------------------------------
            commentsBatch = [ ]
            LOG.debug("Comments for article %d: %d" % (articleId, numComments))
            for ii in xrange(0, numComments):
                #lastDate = randomDate(articleDate, constants.STOP_DATE)
                commentAuthor = random.choice(self.authors)
                commentContent = randomString(constants.COMMENT_CONTENT_SIZE)
                
                comment = {
                    "id": str(articleId)+"|"+str(ii),
                    "article": articleId,
                    "date": randomDate(articleDate, constants.STOP_DATE), 
                    "author": commentAuthor,
                    "comment": commentContent,
                    "rating": int(self.ratingZipf.next())
                }
                commentCtr += 1
                commentsBatch.append(comment) 
                #if config[self.name]["denormalize"]:
                    #self.db[constants.ARTICLE_COLL].update({"id": articleId},{"$push":{"comments":comment}}) 
                    
                if not config[self.name]["denormalize"]:
                    self.db[constants.COMMENT_COLL].insert(comment) 
            ## FOR (comments)
            if config[self.name]["denormalize"]:
	        self.db[constants.ARTICLE_COLL].update({"id": articleId},{"$pushAll":{"comments":commentsBatch}})  
        ## FOR (articles)
        
        if config[self.name]["denormalize"]:
            if articleCtr % 100 == 0 or articleCtr % 100 == 1 :
                self.loadStatusUpdate(articleCtr / articleTotal)
                LOG.info("ARTICLE: %6d / %d" % (articleCtr, articleTotal))

        LOG.info("ARTICLES PER THREAD: %6d / %d" % (articleCtr, articleCtr))
        LOG.info("COMMENTS PER THREAD: %6d / %d" % (commentCtr,commentCtr))        
        LOG.info("TOTAL ARTICLES: %6d / %d" % (self.clientprocs*articleCtr, self.clientprocs*articleCtr))
        LOG.info("TOTAL COMMENTS: %6d / %d" % (self.clientprocs*commentCtr,self.clientprocs*commentCtr))   
    ## DEF
    
    ## ---------------------------------------------------------------------------
    ## EXECUTION INITIALIZATION
    ## ---------------------------------------------------------------------------
    
    def executeInitImpl(self, config):
        pass
    ## DEF
    
    ## ---------------------------------------------------------------------------
    ## WORKLOAD EXECUTION
    ## ---------------------------------------------------------------------------
    
    def next(self, config):
        assert "experiment" in config[self.name]
        
        if config[self.name]["experiment"] == constants.EXP_DENORMALIZATION:
            articleId = random.randint(0, self.num_articles)
            opName = "readArticleTopTenComments"
            return (opName, (articleId,))
            
        elif config[self.name]["experiment"] == constants.EXP_SHARDING:
            trial = int(config[self.name]["sharding"])
            if trial == 0:
                #single sharding key
                articleId = self.articleZipf.next()
                opName = "readArticleById"
                return (opName, (articleId,))
            elif trial == 1:
                #composite sharding key
                articleId = self.articleZipf.next()
                articleSlug = '%064d' % hash(str(articleId))
                opName = "readArticleByIdAndSlug"
                return (opName, (articleId,articleSlug))
               
        elif config[self.name]["experiment"] == constants.EXP_INDEXING:
            trial = int(config[self.name]["indexes"])
            #randreadop = random.randint(1,2)
            #readwriterandom = random.random()
            #readpercent = 0.8
            skewfactor = float(config[self.name]["skew"])
            skewrandom = random.random()
            if skewrandom > skewfactor:
                 #LOG.debug("random~~~")
                 author = self.authors[int(random.randint(0,constants.NUM_AUTHORS-1))] 
                 tag = self.tags[int(random.randint(0,constants.NUM_TAGS-1))]
            else:
                 #LOG.debug("zipfian~~~")
                 #author = self.authors[0]
                 #tag = self.tags[0]
                 author = self.authors[self.authorZipf.next()]
                 tag = self.tags[self.tagZipf.next()] 
            opName = "readArticlesByAuthorAndTag"
            return (opName, (author,tag))
            
            #read = False
            
            #if readwriterandom < readpercent:
                #read = True
            #if read:
                #if randreadop == 1:
                    #if skewrandom > skewfactor:
                        #author = self.authors[int(random.randint(0,constants.NUM_AUTHORS-1))] 
                    #else:
                        #author = self.authors[self.authorZipf.next()] 
                    #opName = "readArticlesByAuthor"
                    #return (opName, (author)) 
                #elif randreadop == 2:
                    #if skewrandom > skewfactor:
                        #tag = self.tags[int(random.randint(0,constants.NUM_AUTHORS-1))]
                    #else:
                        #tag = self.tags[self.authorZipf.next()] 
                    #opName = "readArticlesByTag"
                    #return (opName, (tag))
            #else:
                #if skewrandom > skewfactor:
                    #articleId = random.randint(0, self.num_articles)
                #else:
                    #articleId = self.articleZipf.next()
                ##LOG.info("incViews"+str(articleId))    
                #opName="incViewsArticle" # TODO Fix the warning - it doesn't work
                #return (opName, (articleId)) 
            ##The skew percentage determines which operations we will grab 
            ##an articleId/articleDate using a Zipfian random number generator versus 
            ##a uniform distribution random number generator.
            #skewfactor = float(config[self.name]["skew"])
            #trial = int(config[self.name]["indexes"])
            ##The first trial (0) will consist of 90% reads and 10% writes. 
            ##The second trial (1) will be 80% reads and 20% writes.
            #readwriterandom = random.random()
            #read = False
            #if trial == 0: 
                ##read
                #if readwriterandom < 0.8:
                    #read = True
            #elif trial == 1:
                ##read
                #if readwriterandom < 0.9:
                   #read = True   
            ##if read
            #if read == True:
                ##random 1..3 to see which read we will make
                #randreadop = random.randint(1,3)
                #skewrandom = random.random()
                #if randreadop == 1:
                    #if skewrandom < skewfactor:
                        #articleId = random.randint(0, self.num_articles)
                    #else:
                        #articleId = self.articleZipf.next()
                    #opName = "readArticleById"
                    #return (opName, (articleId))
                #elif randreadop == 2:
                    #if skewrandom < skewfactor: 
                        #date = randomDate(constants.START_DATE, constants.STOP_DATE)
                    #else:
                        #date = self.dates[self.dateZipf.next()] #TODO to fix how to get the right position
                    #opName = "readArticlesByDate"
                    #return (opName, (date))
                #elif randreadop == 3:
                    #if skewrandom < skewfactor:
                        #author = self.authors[int(random.randint(0,constants.NUM_AUTHORS-1))] #TODO to fix
                    #else:
                        #author = self.authors[self.authorZipf.next()] #TODO to fix how to get the right position
                    #opName = "readArticlesByAuthor"
                    #return (opName, (author)) 
                #elif randreadop == 4:
                    #if skewrandom < skewfactor:
                        #date = random.randint(0,constants.NUMBER_OF_DATE_SUBRANGES-1) 
                        #author = self.authors[int(random.randint(0,constants.NUM_AUTHORS-1))] #TODO to fix
                    #else:
                        #date = self.dateZipf.next() #TODO use the DateZipf and make range date queries 
                        #author = self.authors[self.authorZipf.next()] #TODO to fix how to get the right position
                    #opName = "readArticleByAuthorAndDate"
                    #return (opName, (author,date)) 
                
            #if write
            #elif read == False: 
                #skewrandom = random.random()
                #if skewrandom < skewfactor:
                    #articleId = random.randint(0, self.num_articles)
                #else:
                    #articleId = self.articleZipf.next()
                #opName="incViewsArticle"
                #return (opName, (articleId)) 
            #do the increase of views
   ## DEF
        
    def executeImpl(self, config, op, params):
        #global opCount;
        assert self.conn != None
        assert "experiment" in config[self.name]
        
        if self.debug:
            LOG.debug("Executing %s / %s" % (op, str(params)))
        
        m = getattr(self, op)
        assert m != None, "Invalid operation name '%s'" % op
        try:
            result = m(config[self.name]["denormalize"], *params)
            #result = m(*params)
        except:
            LOG.warn("Unexpected error when executing %s" % op)
            raise
        
        return 1 # number of operations
    ## DEF
    
    def readArticleById(self,denormalize, articleId):
        article = self.db[constants.ARTICLE_COLL].find_one({"id": articleId})
        if not article:
            LOG.warn("Failed to find %s with id #%d" % (constants.ARTICLE_COLL, articleId))
            return
        assert article["id"] == articleId, \
            "Unexpected invalid %s record for id #%d" % (constants.ARTICLE_COLL, articleId)
            
    
    def readArticlesByTag(self,denormalize, tag):
        articles = self.db[constants.ARTICLE_COLL].find({"tags": tag})
        for article in articles:
            pass 
    
    def readArticlesByAuthor(self,denormalize,author):
        articles = self.db[constants.ARTICLE_COLL].find({"author": author})
        for article in articles:
            pass     
    
    def readArticlesByDate(self,denormalize,date):
        article = self.db[constants.ARTICLE_COLL].find({"date": date})
        for article in articles:
            pass 
    
    def readArticleByIdAndSlug(self,denormalize,id,slug):
        article = self.db[constants.ARTICLE_COLL].find_one({"id":id,"slug": slug})
        articleId = article["id"]
        if not article:
            LOG.warn("Failed to find %s with id #%d" % (constants.ARTICLE_COLL, articleId))
            return
        assert article["slug"] == slug, \
            "Unexpected invalid %s record for id #%d" % (constants.ARTICLE_COLL, articleId)
        assert article["id"] == id, \
            "Unexpected invalid %s record for id #%d" % (constants.ARTICLE_COLL, articleId)   
    
    
    
    def readArticlesByAuthorAndDate(self,denormalize,author,date):
        articles = self.db[constants.ARTICLE_COLL].find({"author":author,"date": date})
        for article in articles:
            pass  

    def readArticlesByAuthorAndTag(self,denormalize,author,tag):
        #LOG.debug("author~"+str(author))
        #LOG.debug("tag~"+str(tag))
        articles = self.db[constants.ARTICLE_COLL].find({"author":author,"tags": tag})
        for article in articles:
            pass    
        #LOG.debug(str(articles.count()))
    
    def readArticleTopTenComments(self,denormalize,articleId):
        # We are searching for the comments that had been written for the article with articleId 
        # and we sort them in descending order of user rating
        if not denormalize: 
            article = self.db[constants.ARTICLE_COLL].find_one({"id": articleId})
            comments = self.db[constants.COMMENT_COLL].find({"article": articleId}).sort("rating",-1)
            #for comment in comments:
            #    pprint(comment)
            #    print("\n");
            #print("~~~~~~~~~~~~~~");
        else:
            article = self.db[constants.ARTICLE_COLL].find_one({"id": articleId})
            if not article is None:
                assert 'comments' in article, pformat(article)
                comments = article[u'comments']
                #sort by rating descending and take top 10..
                comments = sorted(comments, key=lambda k: -k[u'rating'])
                comments = comments[0:10]
                #pprint(comments)
                #print("\n");
            elif article is None:
                LOG.warn("Failed to find %s with id #%d" % (constants.ARTICLE_COLL, articleId))
                return
            assert article["id"] == articleId, \
                "Unexpected invalid %s record for id #%d" % (constants.ARTICLE_COLL, articleId) 
        
        
    ## DEF
    
    def incViewsArticle(self,denormalize,articleId):
        #Increase the views of an article by one
        self.db[constants.ARTICLE_COLL].update({'id':articleId},{"$inc" : {"views":1}},False)
        return

## CLASS
