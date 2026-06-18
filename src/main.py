import socket
import yaml
import time
import urllib3
import os
import shutil
import requests
import dns.resolver
import random
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

CONFIG_PATH=os.getenv('CONFIG_PATH', './config.yml')
METRICS_RESULT=os.getenv('METRICS_RESULT', './metrics')
GLOBAL_DNS_ERRORS=0

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def generate_metric_string(metric_name,labels,value):
    timestamp_ms = int(time.time() * 1000)
    result=f"{metric_name}"
    if labels!={}:
        result+="{"
        for key in labels.keys():
            result+=f"{key}=\"{labels[key]}\","
        result=result[:-1]
        result+="}"
    result+=f" {value} {timestamp_ms}"
    return result

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
    metric_labels={
      'domain_name': check_domain_name,
      'port': check_port,
      "check_id": check_item.get('check_id'),
      'metric': 'success_check'
    }
    if IPs==[]:
        timestamp_ms = int(time.time() * 1000)
        print(generate_metric_string('kia_http_check',metric_labels,'0'),file=metrics_file)
        return
    for IP in IPs:
        metric_labels['IP']=IP
        print(f"Check '{check_domain_name}' on IP '{IP}'...")
        response=http_request(check_item,IP,check_scheme,check_port,custom_headers,check_url)
        if response!={}:
            metric_labels['metric']='status_code'
            print(generate_metric_string('kia_http_check',metric_labels,response.status_code),file=metrics_file)
            metric_labels['metric']='success_check'
            print(generate_metric_string('kia_http_check',metric_labels,check_http_reponce(check_item,response)),file=metrics_file)
        else:
            print("Request failed")
            print(generate_metric_string('kia_http_check',metric_labels,'0'),file=metrics_file)

def dns_check(check_item,metrics_file):
    global GLOBAL_DNS_ERRORS
    for dns_server in check_item['dns_servers']:
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = [ dns_server ]
        resolver.lifetime = 2.0
        for domain in check_item['domains']:
            try:
                print(f"NS={dns_server} --> DOMAIN={domain}")
                timestamp_ms = int(time.time() * 1000)
                resolver_answer=resolver.resolve(domain, 'A')
                print(", ".join([rdata.address for rdata in resolver_answer]))
                print('kia_dns_probe{dns_server="',dns_server,'",domain="',domain,'", metric="success_check"} 1 ',timestamp_ms,file=f,sep='')
            except Exception as e:
                GLOBAL_DNS_ERRORS+=1
                print('kia_dns_probe{dns_server="',dns_server,'",domain="',domain,'", metric="success_check"} 0 ',timestamp_ms,file=f,sep='')
                print(f"ОШИБКА DNS: {type(e).__name__} — {e}")

def request_via_proxy(task):
    try:
        task_id=task['task_id']
        proxy_protocol=task['labels']['proxy_protocol']
        proxy_user=task['proxy_auth']['user']
        proxy_pass=task['proxy_auth']['password']
        proxy_ip=task['labels']['IP']
        proxy_port=task['labels']['proxy_port']
        proxy_string=f"{proxy_protocol}://{proxy_user}:{proxy_pass}@{proxy_ip}:{proxy_port}"
    
        proxy_auth=task['proxy_auth']
        print(f"proxy_string='{proxy_string}'; TASK_ID={task_id}")
        session = requests.Session()
        session.proxies = {
            "http": proxy_string,
            "https": proxy_string
        }
        for custom_header in task['add_headers']:
            header_content=[item.strip() for item in custom_header.split(':',1)]
            session.headers.update({header_content[0]:header_content[1]})
        if 'add_header' in proxy_auth:
            for custom_header in proxy_auth['add_headers']:
                header_content=[item.strip() for item in custom_header.split(':',1)]
                session.headers.update({header_content[0]:header_content[1]})
        if 'add_labels' in proxy_auth:
            for custom_label in proxy_auth['add_labels']:
                label_content=[item.strip() for item in custom_label.split(':',1)]
                task['labels'][label_content[0]]=label_content[1]
        print(f"TASK_ID='{task_id}' --> Finished")
        response=session.get(task['labels']['url'],timeout=int(task['labels']['timeout']))
        if task['labels']['status_code']=='ANY' or response.status_code == task['labels']['status_code']:
            return generate_metric_string(task['metric_name'],task['labels'],1)
        else:
            print(f"TASK_ID='{task_id} --> Response code is not '{task['labels']['status_code']}' ({response.status_code})")
            return generate_metric_string(task['metric_name'],task['labels'],0)
    except Exception as e:
        print(f"TASK_ID='{task_id} --> Error: {e}")
        return generate_metric_string(task['metric_name'],task['labels'],0)
        return 

def proxy_list_check(proxy_checks_list,global_config,f):
    task_list=[]
    for proxy_check_item in proxy_checks_list:
        labels={
          'IP': '',
          'url': proxy_check_item['url'],
          'status_code': proxy_check_item.get('status_code',200),
          'metric': 'success_check',
          'proxy_port': proxy_check_item['proxy_port'],
          'timeout': proxy_check_item.get('timeout',3),
          'proxy_protocol': proxy_check_item.get('proxy_protocol','http')
        }

        if 'add_labels' in proxy_check_item:
            for custom_label in proxy_check_item['add_labels']:
                z_label=[item.strip() for item in custom_label.split(':',1)]
                labels[z_label[0]]=z_label[1]

        for IP in proxy_check_item['proxy_ips']:
            labels['IP']=IP
            for proxy_auth in proxy_check_item['proxy_auth']:
                task_id=str(uuid.uuid4())
                labels['id_label']=proxy_auth['id_label']
                task_list+=[
                  {
                    "task_id": task_id,
                    "metric_name": "kia_proxy_check",
                    "labels": dict(labels),
                    "proxy_auth": dict(proxy_auth),
                    "add_headers": proxy_check_item.get('add_headers',[]),
                  }
                ]
                print(task_list[len(task_list)-1])
    with ThreadPoolExecutor(max_workers=global_config.get('proxy_checks_parralel',100)) as executor:
        results = executor.map(request_via_proxy, task_list)
        final_results = list(results)
    for res in final_results:
        print(res,file=f)

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
    timestamp_ms = int(time.time() * 1000)
    print('kia_global_errors ',GLOBAL_DNS_ERRORS,' ',timestamp_ms,file=f,sep='')
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

    if proxy_checks!=[]:
        print('Proxy checks')
        f=open('/tmp/proxy_results','w')
        proxy_list_check(proxy_checks,config,f)
        f.close()

    ends=int(time.time())-start
    timestamp_ms = int(time.time() * 1000)
    f=open('/tmp/proxy_results','a')
    print(f"blaster_version 0.51 {timestamp_ms}",file=f)
    print(f"kia_cicle_time_seconds {ends} {timestamp_ms}",file=f)
    print(f"blaster_last_update {timestamp_ms}", file=f)
    f.close()

    os.system('cat /tmp/proxy_results >> /tmp/results')

    shutil.move('/tmp/results',METRICS_RESULT)
    delta_sleep=cicle_timeout-ends
    if delta_sleep<0:
        print(f'Warning. Cicle was too long >{cicle_timeout} --> delta_sleep=0')
        delta_sleep=0
    print(f"Cicle ends. Wait for {delta_sleep} seconds for resume...")
    time.sleep(delta_sleep)