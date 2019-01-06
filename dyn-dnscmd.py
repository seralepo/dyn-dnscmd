#!/usr/bin/python3
#
# Command line tool for editing external DNS records via DynDNS API.
# Author: Sergey Polyakov <sergey.polyakov@nordigy.ru>
# Documentation: https://help.dyn.com/understanding-works-api/
#

import sys
import cmd
import getpass
import requests

DynectURL = 'https://api.dynect.net'

URI = {
    'login':            '/REST/Session/?customer_name={0}&user_name={1}&password={2}',
    'logout':           '/REST/Session/',
    'record':           '/REST/{0}Record/{1}/{2}/{3}',
    'zone_publish':     '/REST/Zone/{0}?publish=true',
    'zones':            '/REST/Zone'
}

rdata_map = {
    'CNAME':  'cname',
    'A':      'address',
    'AAAA':   'address',
    'PTR':    'ptrdname',
    'MX':     'exchange',
    'TXT':    'txtdata',
    'NS':     'nsdname'
}

class DynectSession:
    # headers dict holds headers for all HTTP requests
    headers = {'Auth-Token': None, 'Content-Type': 'application/json'}
    # Zone is string like 'example.org'
    Zone = None
    
    def __init__(self, **kwargs):
        # check if auth_token already passed as argument:
        if 'auth_token' in kwargs.keys():
            self.headers['Auth-Token'] = kwargs['auth_token']

    def __enter__(self):
        return self

    def __exit__(self, typ, val, tb):
        if self.headers['Auth-Token']:
            self.Logout()

    # get auth token
    def Login(self, **kwargs):
        customer_name = kwargs['customer_name']
        user_name = kwargs['user_name']
        password = kwargs['password']
        url = DynectURL + URI['login'].format(customer_name, user_name, password)
        resp = requests.post(url, headers=self.headers).json()
        if resp['status'] != 'success':
            raise Exception('Login failed: ' + str(resp['msgs']))
        self.headers['Auth-Token'] = resp['data']['token']

    # logout and forget auth token 
    def Logout(self):
        url = DynectURL + URI['logout']
        resp = requests.delete(url, headers=self.headers).json()
        if resp['status'] != 'success':
            raise Exception('Logout failed: ' + str(resp['msgs']))
        else:
            self.headers['Auth-Token'] = None

    # Returns current auth-token 
    def Token(self):
        return self.headers['Auth-Token']

    # get list of records with the same name and rtype
    def GetRecordSet(self, record, rtype):
        rtype = rtype.upper()
        # get record URIs in format /REST/<rtype>Record/<zone>/<fqdn>/<id>
        url = DynectURL + URI['record'].format(rtype, self.Zone, record, '')
        resp = requests.get(url, headers=self.headers).json()
        recordURIs = [ uri for uri in resp['data'] ]  
        # get record details
        records = []
        for uri in recordURIs:
            url = DynectURL + uri
            resp = requests.get(url, headers=self.headers).json()
            records.append(resp['data'])
        return records

    # like GetRecordSet, but throws exception if multiple records found, returns single record
    def GetRecord(self, record, rtype):
        rtype = rtype.upper()
        # get record URIs in format /REST/<rtype>Record/<zone>/<fqdn>/<id>
        url = DynectURL + URI['record'].format(rtype, self.Zone, record, '')
        resp = requests.get(url, headers=self.headers).json()
        if len(resp['data']) > 1:
            raise Exception('Multiple record IDs returned for {0}.'.format(record))  
        # get record details
        url = DynectURL + resp['data'][0]
        resp = requests.get(url, headers=self.headers).json()
        return resp['data']

    # this doesn't through exception if multiple records found, but requires exact rdata to be specified
    def GetRecordID(self, record, rtype, rdata):
        records = self.GetRecordSet(record, rtype)
        try:
            record_id = [ str(r['record_id']) for r in records if r['rdata'][rdata_map[rtype]].strip('.') == rdata.strip('.') ][0]
        except:
            raise Exception('Record not found.')
        return record_id

    def CreateRecord(self, record, rtype, rdata, **kwargs):
        url = DynectURL + URI['record'].format(rtype, self.Zone, record, '')
        body = {'rdata':{rdata_map[rtype]:rdata}}
        if 'ttl' in kwargs.keys():
            body.update({'ttl':kwargs.pop('ttl')})
        body['rdata'].update(kwargs)
        resp = requests.post(url, headers=self.headers, json=body).json()
        if resp['status'] != 'success':
            raise Exception('Create failure: ' + str(resp['msgs']))

    def UpdateRecord(self, record, rtype, rdata, **kwargs):
        record_id = self.GetRecord(record, rtype)['record_id']
        url = DynectURL + URI['record'].format(rtype, self.Zone, record, record_id)
        body = {'rdata':{rdata_map[rtype]:rdata}}
        if 'ttl' in kwargs.keys():
            body.update({'ttl':kwargs.pop('ttl')})
        body['rdata'].update(kwargs)
        resp = requests.put(url, headers=self.headers, json=body).json()
        if resp['status'] != 'success':
            raise Exception('Update failure: ' + str(resp['msgs']))

    def DeleteRecord(self, record, rtype, rdata):
        record_id = self.GetRecordID(record, rtype, rdata)
        url = DynectURL + URI['record'].format(rtype, self.Zone, record, record_id)
        resp = requests.delete(url, headers=self.headers).json()
        if resp['status'] != 'success':
            raise Exception('Update failure: ' + str(resp['msgs']))

    def GetZones(self):
        url = DynectURL + URI['zones']
        resp = requests.get(url, headers=self.headers).json()
        if resp['status'] != 'success':
            raise Exception('Update failure: ' + str(resp['msgs']))
        zones = [ zone.split('/')[-2] for zone in resp['data'] ]
        return zones

    # always publish your changes!
    def Publish(self):
        url = DynectURL + URI['zone_publish'].format(self.Zone)
        resp = requests.put(url, headers=self.headers).json()
        if resp['status'] != 'success':
            raise Exception('Publish failure: ' + str(resp['msgs']))
        

# global dynect session object
dyn = DynectSession()

# set of zones that changes were made in
affected_zones = set()

# customer name is usually always the same
default_customer_name = 'ringcentral'

# this just returns zone which the record belongs to
def get_zone(fqdn, zones):
    nodes = fqdn.split('.')
    for i in list(range(len(nodes))):
        zone = '.'.join(nodes[i:])
        if zone in zones:
            return zone
    # if no zone found
    return ''

# parses csvline into plain list of arguments
def parse_args(csvline):
    separator = ' '
    escape_substitutor = '%!:SPACE&^*'
    csvline = csvline.replace('\\'+separator, escape_substitutor)
    args = [ a for a in csvline.split(separator) if a != '' ]
    return [ a.replace(escape_substitutor, ' ') for a in args ]

# command line interface class
class Cli(cmd.Cmd):

    def __init__(self):
        cmd.Cmd.__init__(self)
        self.prompt = '> '
        self.ruler = ''
        self.intro = 'For help type \'help\'.'
        self.doc_header = "Interactive command line tool for editing external DNS records via DynDNS API.\nTo get help on a command type 'help <command>'. To exit type 'exit'\nAvailable commands:"
        self.managed_zones = list()

    def emptyline(self):
        # do nothing if empty line is input
        pass

    def preloop(self):
        # download manged zones
        if not self.managed_zones:
            try:
                self.managed_zones = dyn.GetZones()
            except Exception as E:
                print(E)

    def do_get(self, line):
        # parse args
        args = parse_args(line)
        if len(args) != 2:
            print("Bad number of arguments. See 'help get'.")
            return
        fqdn = args[0].strip('.')
        rtype = args[1].upper()
        # get result
        zone = get_zone(fqdn, self.managed_zones)
        if zone == '':
            print('Zone not found for '+fqdn)
            return
        else:
            dyn.Zone = zone
        try:
            print(dyn.GetRecordSet(fqdn, rtype))
        except Exception as E:
            print(E)

    def do_add(self, line):
        # parse args
        args = parse_args(line)
        if len(args) != 4:
            print("Bad number of arguments. See 'help add'.")
            return
        fqdn = args[0].strip('.')
        rtype = args[1].upper()
        if not rtype in rdata_map.keys():
            print('This rtype is not allowed.')
            return
        rdata = args[2]
        try:
            ttl = int(args[3])
        except:
            print('TTL is not numeric.')
            return
        zone = get_zone(fqdn, self.managed_zones)
        if zone == '':
            print('Zone not found for '+fqdn)
            return
        else:
            dyn.Zone = zone
        # apply change
        try:
            if rtype == 'MX':
                dyn.CreateRecord(fqdn, rtype, rdata, ttl=ttl, preference=10)
            else:
                dyn.CreateRecord(fqdn, rtype, rdata, ttl=ttl)
            affected_zones.add(zone)
            print('{0} record for {1} added. Push changes to apply.'.format(rtype, fqdn))
        except Exception as E:
            print(E)
        return

    def do_del(self, line):
        # parse args
        args = parse_args(line)
        if len(args) != 3:
            print("Bad number of arguments. See 'help del'.")
            return
        fqdn = args[0].strip('.')
        rtype = args[1].upper()
        if not rtype in rdata_map.keys():
            print('This rtype is not allowed.')
            return
        rdata = args[2]
        zone = get_zone(fqdn, self.managed_zones)
        if zone == '':
            print('Zone not found for '+fqdn)
            return
        else:
            dyn.Zone = zone
        # apply change
        try:
            dyn.DeleteRecord(fqdn, rtype, rdata)
            affected_zones.add(zone)
            print('{0} record for {1} deleted. Push changes to apply.'.format(rtype, fqdn))
        except Exception as E:
            print(E)
        return

    def do_push(self, args):
        if not affected_zones:
            print('Nothing to push.')
            return
        # set of successfully pushed zones
        pushed_zones = set()
        # iterate through affected zones and try to push them
        for zone in affected_zones:
            dyn.Zone = zone
            try:
                dyn.Publish()
                print('Zone {0} successfully pushed.'.format(zone))
                pushed_zones.add(zone)
            except Exception as E:
                print('Failed to push zone ' + zone, end = ': ')
                print(E)
        # remove successfully pushed zones from affected zones
        affected_zones.difference_update(pushed_zones)

    def do_exit(self, args):
        print('Logging out...')
        try:
            dyn.Logout()
        except:
            pass
        finally:
            sys.exit(0)

    def do_EOF(self, line):
        print('EOF')
        self.do_exit('')

    def help_get(self):
        print('Get and print record details.')
        print('Syntax: get fqdn rtype')

    def help_add(self):
        print("Add record. Use 0 TTL to apply default. Escape all spaces in TXT data with '\\'")
        print('Syntax: add fqdn rtype rdata ttl')

    def help_del(self):
        print("Delete record. Escape all spaces in TXT data with '\\'")
        print('Syntax: del fqdn rtype rdata')

    def help_push(self):
        print('Causes all pending changes to become part of the zone. Data is pushed out to the nameservers.')

    def help_exit(self):
        print('Cancel pending changes, disable auth token and exit.')

    def default(self, line):
        print('Unrecognized command: '+line)
        print("Type 'help' for info.")

    #################
    ## MAIN SCRIPT ##
    #################

if __name__ == '__main__':
    cli = Cli()

    # authenticate to dyndns
    print('Login to {0}'.format(DynectURL))
    customer = input('Enter customer name ({0}): '.format(default_customer_name))
    if customer == '':
        customer = default_customer_name
    user = input('Enter your username in DynDNS: ')
    pw = getpass.getpass()
    try:
        dyn.Login(customer_name=customer, user_name=user, password=pw)
        print('Successfully logged in.')
    except Exception as E:
        print(E)
        sys.exit(1)

    # run command line loop
    while True:
        try:
            cli.cmdloop()
        except KeyboardInterrupt:
            print('^C')

