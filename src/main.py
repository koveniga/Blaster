import socket
import yaml
import time
import urllib3
import os
import shutil
import requests
import dns.resolver
from datetime import datetime

CONFIG_PATH=os.getenv('CONFIG_PATH', './config.yml')
METRICS_RESULT=os.getenv('METRICS_RESULT', './metrics')

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def check_port_v6(host, port, timeout=3):
    try:
        addr_info = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        family, socktype, proto, canonname, sockaddr = addr_info[0]

        with socket.socket(family, socktype, proto) as sock:
            sock.settimeout(timeout)
            sock.connect(sockaddr)
        return 1
    except (socket.timeout, ConnectionRefusedError):
        return 0
    except Exception as e:
        print(f"Ошибка: {e}")
        return 0

def resolve_hostname(hostname):
    try:
        addr_info = socket.getaddrinfo(hostname, None)
        ips = set(item[4][0] for item in addr_info)
        return ips
    except Exception as e:
        print(f"Ошибка резолва: {e}")
        return []

def filter_resolve(IPs,filter):
    result=[]
    if filter=='':
        result=IPs
    elif filter=='ipv4':
        for IP in IPs:
            if ':' not in IP:
                result.append(IP)
    elif filter=='ipv6':
            if ':' in IP:
                result.append(IP)
    return result

def load_configuration():
    with open(CONFIG_PATH, 'r') as file:
        return yaml.safe_load(file)

def simple_check(check_item,metrics_file):
    check_domain_name=check_item.get('domain_name')
    check_port=int(check_item.get('port'))
    check_timeout=int(check_item.get('timeout',3))
    IPs=filter_resolve(resolve_hostname(check_domain_name),check_item.get('filter_resolve',''))
    if IPs==[]:
        timestamp_ms = int(time.time() * 1000)
        print('kia_simple_check{domain_name="',check_domain_name,'", port="',check_port,'", metric="success_check"} 0',' ',timestamp_ms,sep="",file=metrics_file)
        return
    for IP in IPs:
        print('Checking:',IP,check_port,check_timeout)
        check=check_port_v6(IP,check_port,check_timeout)
        timestamp_ms = int(time.time() * 1000)
        print('kia_simple_check{domain_name="',check_domain_name,'", IP="',IP,'", port="',check_port,'", metric="success_check", check_timeout="',check_timeout,'"} ',check,' ',timestamp_ms,sep="",file=metrics_file)

def http_request(check_item,IP,scheme,port,custom_headers,url):
    try:
        if 'basic_auth' in check_item:
            username=check_item['basic_auth']['username']
            password=check_item['basic_auth']['password']
            response=requests.request(
                method=check_item.get('method','GET'),
                url=f"{scheme}://{IP}:{port}{url}",
                auth=(username,password),
                headers=custom_headers,
                timeout=int(check_item.get('timeout',3)),
                allow_redirects=check_item.get('follow_redirects',False),
                verify=False
            )
        else:
            response=requests.request(
                method=check_item.get('method','GET'),
                url=f"{scheme}://{IP}:{port}{url}",
                headers=custom_headers,
                timeout=int(check_item.get('timeout',3)),
                allow_redirects=check_item.get('follow_redirects',False),
                verify=False
            )
        return response
    except Exception as e:
        print(f"Ошибка: {e}")
        return {}

def check_http_reponce(check_item,response):
    check_status_code=str(check_item.get('status_code',200))
    check_target_data=check_item.get('target_data','')
    if str(response.status_code) != check_status_code:
        print(f"{response.status_code} != {check_status_code}")
        return 0
    if 'target_scheme' in check_item:
        if not response.url.startswith(f"{check_item['target_scheme']}://"):
            print(f"Target scheme is not '{check_item['target_scheme']}'")
            return 0
    if check_target_data == '':
        return 1
    if check_status_code in ['301','302','307','308']:
        if response.headers.get("Location") == check_target_data:
            return 1
        else:
            print('response.headers.get("Location") != check_target_data')
            return 0
    if check_target_data in response.text:
        return 1
    else:
        print(f"'check_target_data' not found in response.text")
        return 0

def http_check(check_item,metrics_file):
    check_domain_name=check_item.get('domain_name')
    check_scheme=check_item.get('scheme','http')
    if 'port' not in check_item:
        if check_scheme=='http':
            check_port=80
        elif check_scheme=='https':
            check_port=443
    else:
        check_port=int(check_item['port'])
    check_url=check_item.get('url','/')
    custom_headers={
      'Host': check_domain_name,
      'User-Agent': check_item.get('user_agent','nstb-zabbix')
    }

    IPs=filter_resolve(resolve_hostname(check_domain_name),check_item.get('filter_resolve',''))
    if IPs==[]:
        timestamp_ms = int(time.time() * 1000)
        print('kia_http_check{domain_name="',check_domain_name,'", port="',check_port,'", metric="success_check"} 0',' ',timestamp_ms,sep="",file=metrics_file)
        return
    for IP in IPs:
        print(f"Check '{check_domain_name}' on IP '{IP}'...")
        response=http_request(check_item,IP,check_scheme,check_port,custom_headers,check_url)
        timestamp_ms = int(time.time() * 1000)
        if response!={}:
            print('kia_http_check{domain_name="',check_domain_name,'", IP="',IP,'", port="',check_port,'",metric="status_code"} ',response.status_code,' ',timestamp_ms,sep="",file=metrics_file)
            print('kia_http_check{domain_name="',check_domain_name,'", IP="',IP,'", port="',check_port,'",metric="success_check"} ', check_http_reponce(check_item,response),' ',timestamp_ms,sep="",file=metrics_file)
        else:
            print("Request failed")
            print('kia_http_check{domain_name="',check_domain_name,'", IP="',IP,'", port="',check_port,'",metric="success_check"} 0 ',timestamp_ms,sep="",file=metrics_file)

def http_request_via_proxy(check_item,proxy_auth,proxy_ip):
    try:
        proxy_string=f"http://{proxy_auth['user']}:{proxy_auth['password']}@{proxy_ip}:{check_item['proxy_port']}"
        session = requests.Session()
        session.proxies = {
            "http": proxy_string,
            "https": proxy_string
        }
        if 'add_headers' in check_item:
            for custom_header in check_item['add_headers']:
                header_content=[item.strip() for item in custom_header.split(':',1)]
                session.headers.update({header_content[0]:header_content[1]})
        return session.get(check_item['url'],timeout=int(check_item.get('timeout',3)))
    except Exception as e:
        print(f"Ошибка: {e}")
        return {}

def proxy_check(check_item,metrics_file,T_IPs=[]):
    if T_IPs == []:
        IPs=filter_resolve(resolve_hostname(check_item['proxy_domain_name']),'')
    else:
        IPs=T_IPs

    if IPs==[]:
        timestamp_ms = int(time.time() * 1000)
        print('kia_proxy_check{domain_name="',check_domain_name,'", metric="success_check"} 0',' ',timestamp_ms,sep="",file=metrics_file)
        return

    for IP in IPs:
        print(f"Check {check_item['url']} via proxy {IP}:{check_item['proxy_port']}...")
        for proxy_auth in check_item['proxy_auth']:
            print(f"Check with username '{proxy_auth['user']}'; id_label={proxy_auth['id_label']}...")
            response=http_request_via_proxy(check_item,proxy_auth,IP)
            timestamp_ms = int(time.time() * 1000)
            if response=={}:
                print('kia_proxy_check{proxy="',check_item['proxy_domain_name'],'", IP="',IP,'",url="',check_item['url'],'",status_code="',check_item['status_code'],'",metric="success_check", id_label="',proxy_auth['id_label'],'"} 0 ',timestamp_ms,file=f,sep="")
            else:
                if str(response.status_code) == str(check_item['status_code']):
                    print('kia_proxy_check{proxy="',check_item['proxy_domain_name'],'", IP="',IP,'",url="',check_item['url'],'",status_code="',check_item['status_code'],'",metric="success_check",id_label="',proxy_auth['id_label'],'"} 1 ',timestamp_ms,file=f,sep="")
                else:
                    print(f"{response.status_code} == {check_item['status_code']}")
                    print('kia_proxy_check{proxy="',check_item['proxy_domain_name'],'", IP="',IP,'",url="',check_item['url'],'",status_code="',check_item['status_code'],'",metric="success_check",login="',proxy_auth['user'],'", id_label="',proxy_auth['id_label'],'"} 0 ',timestamp_ms,file=f,sep="")

def dns_check(check_item,metrics_file):
    for dns_server in check_item['dns_servers']:
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = [ dns_server ]
        resolver.lifetime = 2.0
        for domain in check_item['domains']:
            try:
                print(f"NS={dns_server} --> DOMAIN={domain}")
                timestamp_ms = int(time.time() * 1000)
                resolver.resolve(domain, 'A')
                print('kia_dns_probe{dns_server="',dns_server,'",domain="',domain,'", metric="success_check"} 1 ',timestamp_ms,file=f,sep='')
            except Exception as e:
                print('kia_dns_probe{dns_server="',dns_server,'",domain="',domain,'", metric="success_check"} 0 ',timestamp_ms,file=f,sep='')
                print(f"ОШИБКА DNS: {type(e).__name__} — {e}")

while True:
    print(datetime.now())
    start=int(time.time())
    print('-----')
    config=load_configuration()
    cicle_timeout=config.get('global',{}).get('cicle_timeout',30)
    metrics_file=config.get('global',{}).get('metrics_file','./results')

    simple_checks=config.get('simple_checks',[])
    http_checks=config.get('http_checks',[])
    proxy_checks=config.get('proxy_checks',[])
    dns_checks=config.get('dns_checks',[])

    print('DNS Checks')
    f=open('/tmp/dns_checks_results','w')
    for item in dns_checks:
        print(f"Check {item}")
        dns_check(item,f)
    f.close()
    os.system("cat /tmp/dns_checks_results > /tmp/results")

    print('Simple checks')
    f=open('/tmp/simple_checks_results','w')
    for item in simple_checks:
        print(f"Check {item}")
        simple_check(item,f)
    f.close()
    os.system("cat /tmp/simple_checks_results >> /tmp/results")

    print('HTTP checks')
    f=open('/tmp/http_checks_results','w')
    for item in http_checks:
        print(f"Check {item}")
        http_check(item,f)
    f.close()
    os.system('cat /tmp/http_checks_results >> /tmp/results')

    print('Proxy checks')
    f=open('/tmp/proxy_results','w')
    for item in proxy_checks:
        print(f"Check {item}")
        if 'proxy_domain_names' in item:
            for proxy_domain_name in item['proxy_domain_names']:
                item['proxy_domain_name']=proxy_domain_name
                proxy_check(item,f)
        elif 'proxy_domain_name' in item:
            proxy_check(item,f)
        elif 'proxy_ips' in item:
            item['proxy_domain_name']='CHECK_BY_IP'
            proxy_check(item,f,item['proxy_ips'])
    f.close()

    ends=int(time.time())-start
    timestamp_ms = int(time.time() * 1000)
    f=open('/tmp/proxy_results','a')
    print(f"blaster_version 0.4 {timestamp_ms}",file=f)
    print(f"kia_cicle_time_seconds {ends} {timestamp_ms}",file=f)
    print(f"blaster_last_update {timestamp_ms}", file=f)
    f.close()

    os.system('cat /tmp/proxy_results >> /tmp/results')

    shutil.move('/tmp/results',METRICS_RESULT)
    time.sleep(cicle_timeout)