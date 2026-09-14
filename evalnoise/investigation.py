"""Pure presentation helpers; no execution, artifact mutation, or inference."""
from collections import Counter
import hashlib
import html
import json
import math


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def selected(records, filters):
    result = []
    for file, trial in records:
        text = ' '.join(str(trial.get(k, '')) for k in ('id','task','profile','repeat','status')).lower()
        if (all(not filters.get(k) or str(trial.get(k)) == filters[k] for k in ('profile','status','task'))
                and filters.get('search', '').lower() in text):
            result.append((file, trial))
    return result


def provenance(left, right):
    fields = {'task_contracts': 'identity', 'images': 'identity', 'provider_snapshot': 'identity',
              'measurement_kind': 'identity', 'schema_version': 'identity', 'evalnoise_version': 'conditions',
              'engine_identity': 'conditions', 'engine_identity_final': 'stability'}
    rows = []
    for key, category in fields.items():
        a, b = left.get(key), right.get(key)
        rows.append({'field':key,'category':category,'baseline':a,'candidate':b,
                     'state':'unknown' if a is None or b is None else 'equal' if a == b else 'different'})
    for key in ('tasks','seed','profiles','sample_interval_s','repeats'):
        a, b = left.get('config', {}).get(key), right.get('config', {}).get(key)
        rows.append({'field':'config.'+key,'category':'identity' if key in ('tasks','seed') else 'conditions',
                     'baseline':a,'candidate':b,
                     'state':'unknown' if a is None or b is None else 'equal' if a == b else 'different'})
    incompatible = any(r['category']=='identity' and r['state']=='different' for r in rows)
    unstable = any(m.get('engine_identity_final', {}).get('stable') is not True
                   for m in (left,right) if isinstance(m.get('engine_identity_final'),dict))
    return {'rows':rows,'verdict':'incompatible' if incompatible or unstable else 'requires_analytical_validation',
            'note':'Provenance inspection only, not permission to pool results. Unknown is not equal. '
                   'Use compare with explicit profiles and treatments for validated numerical comparisons.'}


def matrix(records):
    counts = Counter((str(t.get('task')), str(t.get('profile')), str(t.get('status'))) for _,t in records)
    return [{'task':a,'profile':b,'status':c,'count':n} for (a,b,c),n in sorted(counts.items())]


def timeline(trial):
    """Plot only observed points: no interpolation over missing samples or peaks."""
    samples = trial.get('telemetry') or []
    definitions = [('Memory usage','MiB',('memory_stats','usage'),2**20),
                   ('Cumulative CPU usage','seconds',('cpu_stats','cpu_usage','total_usage'),1e9),
                   ('Cumulative throttled time','milliseconds',('cpu_stats','throttling_data','throttled_time'),1e6)]
    plots = []
    for title, unit, keys, divisor in definitions:
        points = []
        missing = 0
        for sample in samples[:3600]:
            raw = sample.get('raw', {})
            value = raw
            for key in keys:
                value = value.get(key) if isinstance(value,dict) else None
            x = sample.get('received_elapsed_s')
            if (not isinstance(x,(int,float)) or isinstance(x,bool) or not isinstance(value,(int,float)) or isinstance(value,bool)
                    or (isinstance(x,int) and x.bit_length()>1023) or (isinstance(value,int) and value.bit_length()>1023)
                    or not math.isfinite(x) or not math.isfinite(value) or x < 0 or value < 0):
                missing += 1
                continue
            points.append((x,value/divisor))
        if not points:
            continue
        xmax = max(x for x,_ in points) or 1
        ymax = max(y for _,y in points) or 1
        dots = ''.join(f'<circle cx="{60+x/xmax*630:.2f}" cy="{175-y/ymax*130:.2f}" r="3"/>' for x,y in points)
        label = html.escape(f'{title} ({unit})')
        plots.append(f'<figure><figcaption>{label}</figcaption><svg viewBox="0 0 740 230" role="img" aria-label="{label}">'
                     f'<path d="M60 40V175H700"/><g>{dots}</g><text x="5" y="48">{ymax:.3g}</text>'
                     f'<text x="35" y="179">0</text><text x="60" y="198">0</text><text x="670" y="198">{xmax:.2f}</text>'
                     '<text x="200" y="222">Host receipt time since trial start (seconds)</text></svg>'
                     f'<p class="meta">Raw Engine samples · {len(points)} measured points · {missing} unavailable samples. '
                     'Points are not peaks; missing observations are not filled. Cumulative counters are not rates.</p></figure>')
    return ''.join(plots) or '<p class="meta">No plottable raw Engine samples. Sampling may be disabled, unavailable, or CLI-formatted; no zero values are invented.</p>'
