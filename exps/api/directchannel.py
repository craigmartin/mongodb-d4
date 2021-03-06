# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------
# Copyright (C) 2012
# Andy Pavlo - http://www.cs.brown.edu/~pavlo/
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
import logging

from messageprocessor import *
from message import *

LOG = logging.getLogger(__name__)

class DirectChannel:
    
    def __init__(self):
        self.gateway = None # Needed by message.py
        self.queue = [ ]
        self.processor = MessageProcessor(self)
        
        m = Message(MSG_NOOP, True)
        self.defaultResponse = pickle.dumps(m, -1)
        self.response = None
        
        pass
    
    def __iter__(self):
        return self

    def next(self):
        if len(self.queue) == 0:
            raise StopIteration
        return self.queue.pop(0)

    def send(self, msg):
        m = getMessage(msg)
        if m.header in [ MSG_INIT_COMPLETED, MSG_LOAD_COMPLETED, MSG_EXECUTE_COMPLETED ]:
            self.response = msg
        else:
            self.queue.append(msg)
            self.processor.processMessage()
    ## DEF
        
    def receive(self):
        r = None
        if self.response != None:
            r = self.response
            self.response = None
        else:
            r = self.defaultResponse
        return r
## CLASS

    