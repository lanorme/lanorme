"""Seed functions for the SIMILAR-001 / DRY-001 adversarial generator.

Copied verbatim from the CPython 3.13.12 standard library (PSF License), so
the seeds are real code that no LaNorme rule was tuned against. SEED_ORIGINS
names the file each one comes from. evals/generate_adversarial.py reads this
file, applies a transform to each seed and writes the pair to
holdout/generated/; nothing here is scored directly.
"""

SEED_ORIGINS = {
    "covariance": "cpython@v3.13.12:Lib/statistics.py",
    "_checknetloc": "cpython@v3.13.12:Lib/urllib/parse.py",
    "urlunsplit": "cpython@v3.13.12:Lib/urllib/parse.py",
    "make_msgid": "cpython@v3.13.12:Lib/email/utils.py",
    "_mac_ver_xml": "cpython@v3.13.12:Lib/platform.py",
    "copyfileobj": "cpython@v3.13.12:Lib/tarfile.py",
    "parse257": "cpython@v3.13.12:Lib/ftplib.py",
    "cleandoc": "cpython@v3.13.12:Lib/inspect.py",
    "long_has_args": "cpython@v3.13.12:Lib/getopt.py",
    "_check_date_fields": "cpython@v3.13.12:Lib/_pydatetime.py",
}


def covariance(x, y, /):
    """Covariance

    Return the sample covariance of two inputs *x* and *y*. Covariance
    is a measure of the joint variability of two inputs.

    >>> x = [1, 2, 3, 4, 5, 6, 7, 8, 9]
    >>> y = [1, 2, 3, 1, 2, 3, 1, 2, 3]
    >>> covariance(x, y)
    0.75
    >>> z = [9, 8, 7, 6, 5, 4, 3, 2, 1]
    >>> covariance(x, z)
    -7.5
    >>> covariance(z, x)
    -7.5

    """
    n = len(x)
    if len(y) != n:
        raise StatisticsError('covariance requires that both inputs have same number of data points')
    if n < 2:
        raise StatisticsError('covariance requires at least two data points')
    xbar = fsum(x) / n
    ybar = fsum(y) / n
    sxy = sumprod((xi - xbar for xi in x), (yi - ybar for yi in y))
    return sxy / (n - 1)


def _checknetloc(netloc):
    if not netloc or netloc.isascii():
        return
    # looking for characters like \u2100 that expand to 'a/c'
    # IDNA uses NFKC equivalence, so normalize for this check
    import unicodedata
    n = netloc.replace('@', '')   # ignore characters already included
    n = n.replace(':', '')        # but not the surrounding text
    n = n.replace('#', '')
    n = n.replace('?', '')
    netloc2 = unicodedata.normalize('NFKC', n)
    if n == netloc2:
        return
    for c in '/?#@:':
        if c in netloc2:
            raise ValueError("netloc '" + netloc + "' contains invalid " +
                             "characters under NFKC normalization")


def urlunsplit(components):
    """Combine the elements of a tuple as returned by urlsplit() into a
    complete URL as a string. The data argument can be any five-item iterable.
    This may result in a slightly different, but equivalent URL, if the URL that
    was parsed originally had unnecessary delimiters (for example, a ? with an
    empty query; the RFC states that these are equivalent)."""
    scheme, netloc, url, query, fragment, _coerce_result = (
                                          _coerce_args(*components))
    if netloc:
        if url and url[:1] != '/': url = '/' + url
        url = '//' + netloc + url
    elif url[:2] == '//':
        url = '//' + url
    elif scheme and scheme in uses_netloc and (not url or url[:1] == '/'):
        url = '//' + url
    if scheme:
        url = scheme + ':' + url
    if query:
        url = url + '?' + query
    if fragment:
        url = url + '#' + fragment
    return _coerce_result(url)


def make_msgid(idstring=None, domain=None):
    """Returns a string suitable for RFC 2822 compliant Message-ID, e.g:

    <142480216486.20800.16526388040877946887@nightshade.la.mastaler.com>

    Optional idstring if given is a string used to strengthen the
    uniqueness of the message id.  Optional domain if given provides the
    portion of the message id after the '@'.  It defaults to the locally
    defined hostname.
    """
    # Lazy imports to speedup module import time
    # (no other functions in email.utils need these modules)
    import random
    import socket

    timeval = int(time.time()*100)
    pid = os.getpid()
    randint = random.getrandbits(64)
    if idstring is None:
        idstring = ''
    else:
        idstring = '.' + idstring
    if domain is None:
        domain = socket.getfqdn()
    msgid = '<%d.%d.%d%s@%s>' % (timeval, pid, randint, idstring, domain)
    return msgid


def _mac_ver_xml():
    fn = '/System/Library/CoreServices/SystemVersion.plist'
    if not os.path.exists(fn):
        return None

    try:
        import plistlib
    except ImportError:
        return None

    with open(fn, 'rb') as f:
        pl = plistlib.load(f)
    release = pl['ProductVersion']
    versioninfo = ('', '', '')
    machine = os.uname().machine
    if machine in ('ppc', 'Power Macintosh'):
        # Canonical name
        machine = 'PowerPC'

    return release, versioninfo, machine


def copyfileobj(src, dst, length=None, exception=OSError, bufsize=None):
    """Copy length bytes from fileobj src to fileobj dst.
       If length is None, copy the entire content.
    """
    bufsize = bufsize or 16 * 1024
    if length == 0:
        return
    if length is None:
        shutil.copyfileobj(src, dst, bufsize)
        return

    blocks, remainder = divmod(length, bufsize)
    for b in range(blocks):
        buf = src.read(bufsize)
        if len(buf) < bufsize:
            raise exception("unexpected end of data")
        dst.write(buf)

    if remainder != 0:
        buf = src.read(remainder)
        if len(buf) < remainder:
            raise exception("unexpected end of data")
        dst.write(buf)
    return


def parse257(resp):
    '''Parse the '257' response for a MKD or PWD request.
    This is a response to a MKD or PWD request: a directory name.
    Returns the directoryname in the 257 reply.'''
    if resp[:3] != '257':
        raise error_reply(resp)
    if resp[3:5] != ' "':
        return '' # Not compliant to RFC 959, but UNIX ftpd does this
    dirname = ''
    i = 5
    n = len(resp)
    while i < n:
        c = resp[i]
        i = i+1
        if c == '"':
            if i >= n or resp[i] != '"':
                break
            i = i+1
        dirname = dirname + c
    return dirname


def cleandoc(doc):
    """Clean up indentation from docstrings.

    Any whitespace that can be uniformly removed from the second line
    onwards is removed."""
    lines = doc.expandtabs().split('\n')

    # Find minimum indentation of any non-blank lines after first line.
    margin = sys.maxsize
    for line in lines[1:]:
        content = len(line.lstrip(' '))
        if content:
            indent = len(line) - content
            margin = min(margin, indent)
    # Remove indentation.
    if lines:
        lines[0] = lines[0].lstrip(' ')
    if margin < sys.maxsize:
        for i in range(1, len(lines)):
            lines[i] = lines[i][margin:]
    # Remove any trailing or leading blank lines.
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return '\n'.join(lines)


def long_has_args(opt, longopts):
    possibilities = [o for o in longopts if o.startswith(opt)]
    if not possibilities:
        raise GetoptError(_('option --%s not recognized') % opt, opt)
    # Is there an exact match?
    if opt in possibilities:
        return False, opt
    elif opt + '=' in possibilities:
        return True, opt
    # No exact match, so better be unique.
    if len(possibilities) > 1:
        # XXX since possibilities contains all valid continuations, might be
        # nice to work them into the error msg
        raise GetoptError(_('option --%s not a unique prefix') % opt, opt)
    assert len(possibilities) == 1
    unique_match = possibilities[0]
    has_arg = unique_match.endswith('=')
    if has_arg:
        unique_match = unique_match[:-1]
    return has_arg, unique_match


def _check_date_fields(year, month, day):
    year = _index(year)
    month = _index(month)
    day = _index(day)
    if not MINYEAR <= year <= MAXYEAR:
        raise ValueError('year must be in %d..%d' % (MINYEAR, MAXYEAR), year)
    if not 1 <= month <= 12:
        raise ValueError('month must be in 1..12', month)
    dim = _days_in_month(year, month)
    if not 1 <= day <= dim:
        raise ValueError('day must be in 1..%d' % dim, day)
    return year, month, day
