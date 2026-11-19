#!/usr/bin/env python3
"""Regenerate the reference SVG with Python's standard library."""
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent
parts = ['''<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1320" viewBox="0 0 1600 1320" role="img" aria-labelledby="title desc">
<title id="title">Michelangelo on OCI reference architecture</title>
<desc id="desc">All-in-one release 0.13.0-oci.1. Public load balancer forwards TCP to Caddy and OAuth2 Proxy on private OKE nodes. OCI IAM authenticates users. Studio, API, controllers and Temporal coordinate Ray training and private Triton serving. MySQL persists metadata, workflow state and identities. Object Storage stores artifacts and lifecycle logs; OCIR holds images. A public build host installs through the private OKE API. Optional OCI Health Checks monitors the public edge and Monitoring sends alarms through OCI Notifications. The default topology has one system node and two CPU workers; select availability and capacity settings for your workload.</desc>
<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#356b85"/></marker></defs>
<style>text{font-family:Arial,Helvetica,sans-serif;fill:#172b3a}.title{font-size:36px;font-weight:700}.subtitle{font-size:18px;fill:#526674}.zone{font-size:17px;font-weight:700;letter-spacing:.5px}.heading{font-size:20px;font-weight:700}.body{font-size:17px}.small{font-size:15px;fill:#526674}.label{font-size:15px;fill:#294f66}</style>
<rect width="1600" height="1320" fill="#fff"/>
<rect x="40" y="36" width="6" height="68" fill="#b64031"/>
''']


def text(x, y, value, cls='body'):
    parts.append(f'<text x="{x}" y="{y}" class="{cls}">{escape(value)}</text>')


def rect(x, y, w, h, fill, stroke, radius=12, dash=''):
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="1.5" stroke-dasharray="{dash}"/>')


def box(x, y, w, h, title, lines, fill='#fff', stroke='#91a8b8'):
    rect(x, y, w, h, fill, stroke)
    text(x+18, y+30, title, 'heading')
    for i, line in enumerate(lines):
        text(x+18, y+58+i*24, line)


labels = []

def edge(points, label='', lx=0, ly=0, dashed=False, both=False):
    color = '#906433' if dashed else '#356b85'
    attrs = ' stroke-dasharray="7 5"' if dashed else ''
    attrs += ' marker-start="url(#arrow)"' if both else ''
    parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2" marker-end="url(#arrow)"{attrs}/>')
    if label:
        start = len(parts)
        rect(lx-3, ly-15, len(label)*7.8+6, 20, '#fff', '#fff', radius=2)
        text(lx, ly, label, 'label')
        labels.extend(parts[start:])
        del parts[start:]


text(64, 65, 'Michelangelo on OCI', 'title')
text(64, 96, 'Reference architecture • all-in-one deployment • 0.13.0-oci.1', 'subtitle')
box(70, 145, 280, 100, 'Platform users', ['Browser • Studio • API'])
box(650, 145, 430, 100, 'Deployment operator', ['Local Terraform or OCI Resource Manager'])
box(1170, 145, 360, 100, 'OCI IAM identity domain', ['OIDC sign-in • invited users'])
rect(40, 300, 1520, 880, '#f7f9fb', '#91a8b8')
text(60, 328, 'OCI REGION / COMPARTMENT', 'zone')
rect(60, 348, 1120, 648, '#fff', '#91a8b8')
text(80, 376, 'VIRTUAL CLOUD NETWORK', 'zone')
rect(80, 398, 1080, 116, '#fff6ef', '#d5ac86')
text(96, 421, 'PUBLIC EDGE SUBNET', 'zone')
box(100, 434, 330, 64, 'OCI flexible load balancer', ['Reserved IP • TCP 80 / 443'], '#fff', '#d5ac86')
box(760, 434, 375, 64, 'Build / installer host', ['Public VM • operator SSH /32'], '#fff', '#d5ac86')
rect(80, 548, 1080, 254, '#eff6fa', '#8bb0c4')
text(350, 576, 'PRIVATE NODE SUBNET  /  OKE SYSTEM + CPU POOLS', 'zone')
box(100, 602, 230, 168, 'Identity edge', ['Caddy • TLS + ACME', 'OAuth2 Proxy', 'Application identity', 'Roles • sessions'], '#fff', '#8bb0c4')
box(380, 602, 275, 168, 'Platform services', ['Studio UI • Envoy • API', 'Controller • worker', 'Temporal workflows', 'Lifecycle log reader', 'MySQL Router sidecar'], '#fff', '#8bb0c4')
box(705, 602, 200, 168, 'Training / batch', ['KubeRay • Ray jobs', 'CPU examples', 'Artifacts + logs'], '#fff', '#8bb0c4')
box(945, 602, 195, 168, 'Model serving', ['Artifact delivery', 'Triton runtime', 'Private Envoy', 'ClusterIP gateway'], '#fff', '#8bb0c4')
rect(80, 850, 650, 128, '#f2f7f4', '#91b5a0')
text(96, 878, 'PRIVATE ENDPOINT SUBNET', 'zone')
box(100, 895, 330, 66, 'OCI MySQL', ['Metadata • Temporal • identities'], '#fff', '#91b5a0')
box(460, 895, 250, 66, 'Private OKE API', ['Installer + controllers'], '#fff', '#91b5a0')
rect(760, 850, 400, 128, '#f7f9fb', '#91a8b8')
text(778, 878, 'VCN ROUTING', 'zone')
text(778, 910, 'Service gateway → OCI services')
text(778, 938, 'NAT gateway → Internet egress')
text(778, 962, 'Internet gateway → public edge', 'small')
text(1220, 376, 'REGIONAL OCI SERVICES', 'zone')
box(1220, 434, 310, 100, 'Container Registry (OCIR)', ['Private image repository', 'Builder push • runtime pull'])
box(1220, 635, 310, 124, 'Object Storage', ['Private artifacts bucket', 'Models • outputs • run logs', 'S3-compatible HTTPS'])
box(1220, 850, 310, 112, 'Additional persistence', ['TLS certificates: OKE PVC', 'Separate logs bucket created', 'Log bucket not wired to runtime'], '#f7f9fb', '#b7c4cd')

rect(80, 1018, 1080, 142, '#f5f2fa', '#b0a0c0')
text(98, 1040, 'OPTIONAL OCI MONITORING', 'zone')
box(100, 1050, 425, 94, 'Health Checks + Monitoring', ['HTTPS readiness • backend health', 'MySQL backup / storage / missing metrics'], '#fff', '#b0a0c0')
box(650, 1050, 485, 94, 'OCI Notifications', ['Confirmed owner subscription • alarm events', 'Optional budget alerts use the owner email'], '#fff', '#b0a0c0')

# Runtime paths are solid; operator/build paths are dashed.
edge('340,245 340,434', 'HTTPS • DNS hostname', 352, 278)
edge('210,498 210,602', 'TCP passthrough', 222, 538)
edge('330,674 380,674', 'Proxy', 336, 662)
edge('655,656 705,656', 'Jobs', 661, 644)
edge('655,742 675,742 675,786 1040,786 1040,770', 'Publish eligible model', 729, 790)
edge('475,770 475,824 90,824 90,928 100,928', 'Remote SQL: verified TLS', 285, 817)
edge('1350,245 1350,280 570,280 570,532 305,532 305,602', 'OIDC • token verification', 585, 278, both=True)
edge('865,245 865,434', 'Provision + SSH', 877, 278, dashed=True)
edge('948,498 948,521 1148,521 1148,838 585,838 585,895', 'Install via private API', 820, 836, dashed=True)
edge('1135,475 1220,475', 'Push', 1159, 463, dashed=True)
edge('1220,518 1198,518 1198,588 1060,588 1060,602', 'Pull images', 1210, 572)
edge('1140,710 1220,710', 'Read', 1161, 698)
edge('805,770 805,816 1200,816 1200,744 1220,744', 'Write artifacts / logs', 871, 814)
edge('100,1102 72,1102 72,467 100,467', 'HTTPS readiness probe', 110, 1008)
edge('525,1100 650,1100', 'Alarms', 558, 1088)
parts.extend(labels)
text(84, 1206, 'Solid arrows: application traffic and data', 'small')
text(560, 1206, 'Dashed arrows: deployment and image publication', 'small')
text(84, 1244, 'Default topology: one system node + two CPU workers. GPU workloads and high availability require a separate configuration.', 'body')
text(84, 1272, 'Optional OCI alarms and owner notifications. MySQL: seven-day backups + PITR. No project-level team isolation.', 'small')
text(84, 1300, 'Sources: all-in-one Terraform and installer • upstream v0.13.0 + OCI overlays • distribution 0.13.0-oci.1', 'small')
parts.append('</svg>\n')
(OUT / 'architecture.svg').write_text('\n'.join(parts))
print('Generated docs/architecture.svg')
