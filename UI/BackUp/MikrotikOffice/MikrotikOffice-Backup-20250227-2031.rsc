# 2025-02-27 20:31:24 by RouterOS 7.16
# software id = AICV-46L7
#
# model = RB750Gr3
# serial number = HE908X3XFH3
/interface bridge
add ingress-filtering=no name=bridge-vlan vlan-filtering=yes
/interface ethernet
set [ find default-name=ether5 ] advertise=\
    10M-baseT-half,10M-baseT-full,100M-baseT-half,100M-baseT-full l2mtu=1598 \
    mac-address=78:9A:18:7D:D4:37 name=ether2
set [ find default-name=ether4 ] advertise=\
    10M-baseT-half,10M-baseT-full,100M-baseT-half,100M-baseT-full l2mtu=1598 \
    mac-address=78:9A:18:7D:D4:38 name=ether3
set [ find default-name=ether3 ] advertise=\
    10M-baseT-half,10M-baseT-full,100M-baseT-half,100M-baseT-full l2mtu=1598 \
    mac-address=78:9A:18:7D:D4:39 name=ether4
set [ find default-name=ether2 ] advertise=\
    10M-baseT-half,10M-baseT-full,100M-baseT-half,100M-baseT-full l2mtu=1598 \
    mac-address=78:9A:18:7D:D4:3A name=ether5
/interface vlan
add interface=bridge-vlan name=br-vlan-guest-wifi vlan-id=40
add interface=bridge-vlan name=br-vlan-mngmt vlan-id=100
add interface=bridge-vlan name=br-vlan-office-space vlan-id=10
add interface=bridge-vlan name=br-vlan-pos vlan-id=20
add interface=bridge-vlan name=br-vlan-video vlan-id=30
/interface list
add name=LAN
add name=WAN
add name=BLOCK-MNGMT
/ip ipsec profile
add dh-group=modp1024 enc-algorithm=aes-128 hash-algorithm=sha256 name=\
    Site-A-profile
/ip ipsec peer
add address=80.92.238.85/32 name=Site-a-Peer-to-HQ profile=Site-A-profile
/ip ipsec proposal
add auth-algorithms=sha256 enc-algorithms=aes-128-cbc name=Site-A-proposal \
    pfs-group=none
/ip pool
add name=pool-guest-wifi ranges=10.10.40.5-10.10.40.253
add name=pool-office-space ranges=10.36.46.65-10.36.46.125
add name=pool-pos ranges=10.36.46.2-10.36.46.60
/ip dhcp-server
add address-pool=pool-office-space interface=br-vlan-office-space lease-time=\
    10m name=dhcp-office-space
add address-pool=pool-guest-wifi interface=br-vlan-guest-wifi lease-time=10m \
    name=dhcp-guest-wifi
add address-pool=pool-pos interface=br-vlan-pos lease-time=10m name=dhcp-pos
/queue type
add kind=pcq name=pcq-download-3M pcq-classifier=dst-address pcq-rate=3M
add kind=pcq name=pcq-upload-3M pcq-classifier=src-address pcq-rate=3M
/queue simple
add disabled=yes max-limit=30M/30M name=guest-wifi-limit-3M queue=\
    pcq-upload-3M/pcq-download-3M target=10.10.40.0/24
/interface bridge port
add bridge=bridge-vlan ingress-filtering=no interface=ether2
add bridge=bridge-vlan ingress-filtering=no interface=ether3 pvid=20
add bridge=bridge-vlan interface=ether4 pvid=30
add bridge=bridge-vlan interface=ether5 pvid=30
/ip neighbor discovery-settings
set discover-interface-list=!BLOCK-MNGMT
/ip settings
set max-neighbor-entries=2048
/ipv6 settings
set disable-ipv6=yes max-neighbor-entries=8192
/interface bridge vlan
add bridge=bridge-vlan tagged=bridge-vlan,ether2 vlan-ids=10
add bridge=bridge-vlan tagged=bridge-vlan,ether2 vlan-ids=100
add bridge=bridge-vlan tagged=bridge-vlan,ether2 vlan-ids=20
add bridge=bridge-vlan tagged=bridge-vlan,ether2 vlan-ids=30
add bridge=bridge-vlan tagged=bridge-vlan,ether2 vlan-ids=40
/interface list member
add interface=*4 list=WAN
add interface=bridge-vlan list=LAN
add interface=br-vlan-office-space list=LAN
add interface=br-vlan-pos list=LAN
add interface=br-vlan-mngmt list=LAN
add interface=bridge-vlan list=BLOCK-MNGMT
add interface=br-vlan-office-space list=BLOCK-MNGMT
add interface=br-vlan-video list=BLOCK-MNGMT
/ip address
add address=10.36.46.193/26 interface=br-vlan-mngmt network=10.36.46.192
add address=10.36.46.1/26 interface=br-vlan-pos network=10.36.46.0
add address=10.36.46.129/26 interface=br-vlan-video network=10.36.46.128
add address=10.36.46.65/26 interface=br-vlan-office-space network=10.36.46.64
add address=10.10.40.1/24 interface=br-vlan-guest-wifi network=10.10.40.0
/ip cloud
set ddns-enabled=yes ddns-update-interval=5m
/ip dhcp-client
add interface=ether1
/ip dhcp-server lease
add address=10.36.46.13 client-id=1:50:57:9c:d1:21:cb mac-address=\
    50:57:9C:D1:21:CB server=dhcp-pos
add address=10.36.46.12 client-id=1:50:57:9c:d1:21:72 mac-address=\
    50:57:9C:D1:21:72 server=dhcp-pos
add address=10.36.46.2 client-id=1:ec:9a:c:0:9:cd mac-address=\
    EC:9A:0C:00:09:CD server=dhcp-pos
/ip dhcp-server network
add address=10.10.40.0/24 dns-server=8.8.8.8,1.1.1.1 gateway=10.10.40.1
add address=10.36.46.0/26 dns-server=8.8.8.8,1.1.1.1 gateway=10.36.46.1 \
    netmask=26
add address=10.36.46.64/26 dns-server=8.8.8.8,1.1.1.1 gateway=10.36.46.65 \
    netmask=26
/ip dns
set servers=8.8.8.8,1.1.1.1
/ip firewall address-list
add address=194.44.15.214 comment="STZ UARNET" list=mngmt-access-allowed
add address=194.44.223.210 comment="Miskrada UARNET" list=\
    mngmt-access-allowed
add address=91.232.241.216/30 comment="STZ LEOTEL" list=mngmt-access-allowed
add address=10.30.0.0/19 list=mngmt-access-allowed
add address=10.20.30.0/24 list=mngmt-access-allowed
add address=91.232.241.234 comment="STZ LEOTEL1" list=mngmt-access-allowed
add address=194.44.213.156/30 comment="STZ UARNET1" list=mngmt-access-allowed
add address=80.92.238.85 comment="STZ WNET" list=mngmt-access-allowed
add address=192.168.1.0/24 comment=test disabled=yes list=\
    mngmt-access-allowed
add address=10.100.96.0/23 list=stz-local-net
add address=10.72.0.0/23 list=stz-local-net
add address=10.0.3.0/24 list=stz-local-net
add address=78.47.191.120 list=mngmt-access-allowed
/ip firewall filter
add action=accept chain=input comment="Allow Established connections" \
    connection-state=established
add action=accept chain=input disabled=yes dst-port=8291 protocol=tcp \
    src-address-list=mngmt-access-allowed
add action=accept chain=forward disabled=yes protocol=tcp src-address-list=\
    mngmt-access-allowed
add action=drop chain=input comment="blokc access from internet" \
    connection-nat-state=!dstnat connection-state="" disabled=yes \
    in-interface-list=WAN src-address-list=!mngmt-access-allowed
add action=accept chain=input comment="allow managment" disabled=yes \
    src-address-list=mngmt-access-allowed
add action=drop chain=input comment="block mikrotik discovery" disabled=yes \
    in-interface-list=BLOCK-MNGMT
add action=drop chain=forward comment="block mikrotik discovery" disabled=yes \
    in-interface-list=BLOCK-MNGMT out-interface=br-vlan-mngmt
add action=drop chain=input comment="Block access to mngmt" disabled=yes \
    in-interface-list=BLOCK-MNGMT src-address=!10.10.20.222
add chain=forward protocol=tcp tcp-flags=ack,!syn
add action=accept chain=forward connection-state=!related,new
add action=drop chain=input comment="Drop Invalid connections" \
    connection-state=invalid disabled=yes
add action=accept chain=input comment="Allow ICMP" protocol=icmp
add action=drop chain=forward comment="drop invalid connections" \
    connection-state=invalid disabled=yes protocol=tcp
add action=accept chain=forward connection-state=established
add action=accept chain=forward comment="allow related connections" \
    connection-state=related
add action=drop chain=forward disabled=yes src-address=0.0.0.0/8
add action=drop chain=forward disabled=yes dst-address=0.0.0.0/8
add action=drop chain=forward disabled=yes src-address=127.0.0.0/8
add action=drop chain=forward disabled=yes dst-address=127.0.0.0/8
add action=drop chain=forward disabled=yes src-address=224.0.0.0/3
add action=drop chain=forward disabled=yes dst-address=224.0.0.0/3
/ip firewall nat
add action=accept chain=srcnat dst-address-list=stz-local-net \
    out-interface-list=WAN src-address=10.36.46.0/24
add action=dst-nat chain=dstnat comment="rdp acccess to pos 1" dst-port=33002 \
    in-interface-list=WAN protocol=tcp src-address-list=mngmt-access-allowed \
    to-addresses=10.10.20.2 to-ports=3389
add action=dst-nat chain=dstnat comment="rdp acccess to pos 2" dst-port=33003 \
    in-interface-list=WAN protocol=tcp src-address-list=mngmt-access-allowed \
    to-addresses=10.10.20.2 to-ports=3389
add action=dst-nat chain=dstnat comment="rdp acccess to pos 3" dst-port=33004 \
    in-interface-list=WAN protocol=tcp src-address-list=mngmt-access-allowed \
    to-addresses=10.10.20.4 to-ports=3389
add action=dst-nat chain=dstnat comment="rdp acccess to pos 4" dst-port=33005 \
    in-interface-list=WAN protocol=tcp src-address-list=mngmt-access-allowed \
    to-addresses=10.10.20.5 to-ports=3389
add action=dst-nat chain=dstnat comment="rdp acccess to pos 5" dst-port=33006 \
    in-interface-list=WAN protocol=tcp src-address-list=mngmt-access-allowed \
    to-addresses=10.10.20.6 to-ports=3389
add action=dst-nat chain=dstnat comment="rdp acccess to pos 6" dst-port=33007 \
    in-interface-list=WAN protocol=tcp src-address-list=mngmt-access-allowed \
    to-addresses=10.10.20.7 to-ports=3389
add action=dst-nat chain=dstnat comment="rdp acccess to pos 7" dst-port=33008 \
    in-interface-list=WAN protocol=tcp src-address-list=mngmt-access-allowed \
    to-addresses=10.10.20.8 to-ports=3389
add action=dst-nat chain=dstnat comment="rdp acccess to pos 8" dst-port=33009 \
    in-interface-list=WAN protocol=tcp src-address-list=mngmt-access-allowed \
    to-addresses=10.10.20.9 to-ports=3389
add action=dst-nat chain=dstnat comment="rdp acccess to pos 9" dst-port=33010 \
    in-interface-list=WAN protocol=tcp src-address-list=mngmt-access-allowed \
    to-addresses=10.10.20.10 to-ports=3389
add action=dst-nat chain=dstnat comment="rdp acccess to pos 10" dst-port=\
    33011 in-interface-list=WAN protocol=tcp src-address-list=\
    mngmt-access-allowed to-addresses=10.10.20.11 to-ports=3389
add action=dst-nat chain=dstnat comment="ssh acccess to RPI" dst-port=33200 \
    in-interface-list=WAN protocol=tcp src-address-list=mngmt-access-allowed \
    to-addresses=10.10.20.200 to-ports=22
add action=dst-nat chain=dstnat comment="http acccess to switch1" dst-port=\
    22002 in-interface-list=WAN protocol=tcp src-address-list=\
    mngmt-access-allowed to-addresses=10.0.0.2 to-ports=80
add action=dst-nat chain=dstnat comment="http acccess to switch2" dst-port=\
    22003 in-interface-list=WAN protocol=tcp src-address-list=\
    mngmt-access-allowed to-addresses=10.0.0.3 to-ports=80
add action=dst-nat chain=dstnat comment="http acccess to switch3" dst-port=\
    22004 in-interface-list=WAN protocol=tcp src-address-list=\
    mngmt-access-allowed to-addresses=10.0.0.4 to-ports=80
add action=dst-nat chain=dstnat comment="http acccess to switch4" dst-port=\
    22005 in-interface-list=WAN protocol=tcp src-address-list=\
    mngmt-access-allowed to-addresses=10.0.0.5 to-ports=80
add action=dst-nat chain=dstnat comment="http acccess to dvr 1" dst-port=\
    34082 in-interface-list=WAN protocol=tcp src-address-list=\
    mngmt-access-allowed to-addresses=10.10.30.2 to-ports=80
add action=dst-nat chain=dstnat comment="http acccess to dvr 2" dst-port=\
    34083 in-interface-list=WAN protocol=tcp src-address-list=\
    mngmt-access-allowed to-addresses=10.10.30.3 to-ports=80
add action=dst-nat chain=dstnat comment="http acccess to dvr 3" dst-port=\
    34084 in-interface-list=WAN protocol=tcp src-address-list=\
    mngmt-access-allowed to-addresses=10.10.30.4 to-ports=80
add action=dst-nat chain=dstnat comment="37777 acccess to dvr 1" dst-port=\
    34002 in-interface-list=WAN protocol=tcp src-address-list=\
    mngmt-access-allowed to-addresses=10.10.30.2 to-ports=37777
add action=dst-nat chain=dstnat comment="37777 acccess to dvr 2" dst-port=\
    34003 in-interface-list=WAN protocol=tcp src-address-list=\
    mngmt-access-allowed to-addresses=10.10.30.3 to-ports=37777
add action=dst-nat chain=dstnat comment="3777 acccess to dvr 3" dst-port=\
    34004 in-interface-list=WAN protocol=tcp src-address-list=\
    mngmt-access-allowed to-addresses=10.10.30.4 to-ports=37777
add action=dst-nat chain=dstnat comment="3777 acccess to dvr 5" dst-port=\
    34005 in-interface-list=WAN protocol=tcp src-address-list=\
    mngmt-access-allowed to-addresses=10.10.30.5 to-ports=37777
add action=dst-nat chain=dstnat comment="3777 acccess to dvr 6" dst-port=\
    34006 in-interface-list=WAN protocol=tcp src-address-list=\
    mngmt-access-allowed to-addresses=10.10.30.6 to-ports=37777
add action=dst-nat chain=dstnat comment="http acccess to WIFI AP controller" \
    dst-port=24343 in-interface-list=WAN protocol=tcp src-address-list=\
    mngmt-access-allowed to-addresses=10.0.0.101 to-ports=4343
add action=masquerade chain=srcnat comment=masquerading out-interface-list=\
    WAN
/ip hotspot profile
set [ find default=yes ] html-directory=hotspot
/ip ipsec identity
# Suggestion to use stronger pre-shared key or different authentication method
add peer=Site-a-Peer-to-HQ
/ip ipsec policy
add dst-address=10.100.96.0/23 peer=Site-a-Peer-to-HQ proposal=\
    Site-A-proposal src-address=10.36.46.0/24 tunnel=yes
add dst-address=10.72.0.0/23 peer=Site-a-Peer-to-HQ proposal=Site-A-proposal \
    src-address=10.36.46.0/24 tunnel=yes
add dst-address=10.0.3.0/24 peer=Site-a-Peer-to-HQ proposal=Site-A-proposal \
    src-address=10.36.46.0/24 tunnel=yes
/routing bfd configuration
add disabled=no interfaces=all min-rx=200ms min-tx=200ms multiplier=5
/system clock
set time-zone-name=Europe/Kiev
/system identity
set name=lv-mrsh-cherry-r01
/system note
set show-at-login=no
/system ntp client
set enabled=yes
/system ntp server
set enabled=yes multicast=yes
/system ntp client servers
add address=ua.pool.ntp.org
/system resource irq rps
set ether2 disabled=no
set ether3 disabled=no
set ether4 disabled=no
set ether5 disabled=no
/system scheduler
add interval=15m name=ping on-event=ping policy=\
    ftp,reboot,read,write,policy,test,password,sniff,sensitive,romon \
    start-date=2023-12-28 start-time=23:26:03
add interval=1d name=backup on-event=test policy=\
    ftp,reboot,read,write,policy,test,password,sniff,sensitive,romon \
    start-date=2025-01-31 start-time=16:50:00
/system script
add dont-require-permissions=yes name=ping owner=admin policy=\
    ftp,reboot,read,write,policy,test,password,sniff,sensitive,romon source="p\
    ing 10.72.0.1 src-address=10.36.46.1 count=10\r\
    \nping 10.100.96.1 src-address=10.36.46.1 count=10\r\
    \nping 10.0.3.1 src-address=10.36.46.1 count=10"
add dont-require-permissions=no name=test owner=admin policy=\
    ftp,reboot,read,write,policy,test,password,sniff,sensitive,romon source=":\
    global namebackup [/system identity get name]\r\
    \n:global namersc [/system identity get name]\r\
    \n:global backupfinware \"\$namebackup.backup\"\r\
    \n:global backuprsc \"\$namersc.rsc\"\r\
    \n/system backup save name=\$namebackup\r\
    \n:delay 5s\r\
    \n/export file=\$namersc\r\
    \n:delay 5s\r\
    \n/tool fetch address=ftp.kryjivka.com.ua src-path=\$backupfinware user=Mi\
    krotik password=RxJoQUL6X1 upload=yes mode=ftp dst-path=\$backupfinware\r\
    \n:delay 5s\r\
    \n/tool fetch address=ftp.kryjivka.com.ua src-path=\$backuprsc user=Mikrot\
    ik password=RxJoQUL6X1 upload=yes mode=ftp dst-path=\$backuprsc\r\
    \n:delay 5s"
