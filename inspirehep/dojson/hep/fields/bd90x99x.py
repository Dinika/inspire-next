# -*- coding: utf-8 -*-
#
# This file is part of INSPIRE.
# Copyright (C) 2014, 2015, 2016 CERN.
#
# INSPIRE is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# INSPIRE is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with INSPIRE. If not, see <http://www.gnu.org/licenses/>.
#
# In applying this licence, CERN does not waive the privileges and immunities
# granted to it by virtue of its status as an Intergovernmental Organization
# or submit itself to any jurisdiction.

"""MARC 21 model definition."""

from __future__ import absolute_import, division, print_function

import re
from functools import partial

import idutils

from isbn import ISBNError

from dojson import utils

from ..model import hep, hep2marc
from ...utils import (
    force_force_list,
    get_int_value,
    get_recid_from_ref,
    get_record_ref,
)

from inspirehep.modules.references.processors import ReferenceBuilder
from inspirehep.utils.record import get_value


@hep.over('references', '^999C5')
def references(self, key, value):
    """Produce list of references."""
    value = force_force_list(value)

    def get_value(value):
        # Retrieve fields as described here:
        # https://twiki.cern.ch/twiki/bin/view/Inspire/DevelopmentRecordMarkup.
        rb = ReferenceBuilder()
        mapping = [
            ('o', rb.set_number),
            ('m', rb.add_misc),
            ('x', rb.add_raw_reference),
            ('1', rb.set_texkey),
            ('u', rb.add_url),
            ('r', rb.add_report_number),
            ('s', rb.set_pubnote),
            ('p', rb.set_publisher),
            ('y', rb.set_year),
            ('i', rb.add_uid),
            ('b', rb.add_uid),
            ('a', rb.add_uid),
            ('c', rb.add_collaboration),
            ('q', rb.add_title),
            ('t', rb.add_title),
            ('h', rb.add_author),
            ('e', partial(rb.add_author, role='ed.'))
        ]
        for field, method in mapping:
            for element in force_force_list(value.get(field)):
                if element:
                    method(element)

        if '0' in value:
            recid = get_int_value(value, '0')
            rb.set_record(get_record_ref(recid, 'literature'))

        return rb.obj

    references = self.get('references', [])
    references.extend(get_value(v) for v in value)
    return references


@hep2marc.over('999C5', 'references')
@utils.for_each_value
@utils.filter_values
def references2marc(self, key, value):
    """Produce list of references."""
    repnos = value.get('arxiv_eprints', [])
    if 'reportnumber' in value.get('publication_info', {}):
        repnos.append(value['publication_info']['reportnumber'])
    j_title = value.get('publication_info', {}).get('journal_title')
    j_vol = value.get('publication_info', {}).get('journal_volume')
    j_pg_s = value.get('publication_info', {}).get('page_start')
    j_pg_e = value.get('publication_info', {}).get('page_end')
    j_artid = value.get('publication_info', {}).get('artid')
    pubnote = ''
    if j_title and j_vol:
        pubnote = '{},{}'.format(j_title, j_vol)
        if j_pg_s and j_pg_e:
            pubnote += ',{}-{}'.format(j_pg_s, j_pg_e)
        elif j_pg_s:
            pubnote += ',{}'.format(j_pg_s)
        if j_artid and j_artid != j_pg_s:
            pubnote += ',{}'.format(j_artid)
    return {
        '0': get_recid_from_ref(value.get('record')),
        '1': get_value(value, 'texkey'),
        'a': get_value(value, 'publication_info.doi'),
        'c': get_value(value, 'collaboration'),
        'e': [a.get('full_name') for a in value.get('authors', [])
              if a.get('role') == 'ed.'],
        'h': [a.get('full_name') for a in value.get('authors', [])
              if a.get('role') != 'ed.'],
        'm': get_value(value, 'misc'),
        'o': get_value(value, 'number'),
        'i': get_value(value, 'publication_info.isbn'),
        'p': get_value(value, 'imprint.publisher'),
        'r': repnos,
        't': get_value(value, 'titles[:].value'),
        'u': get_value(value, 'urls[:].value'),
        's': pubnote,
        'x': get_value(value, 'raw_reference[:].value'),
        'y': get_value(value, 'publication_info.year')
    }
