# -*- coding: utf-8 -*-
#
# This file is part of INSPIRE.
# Copyright (C) 2014-2017 CERN.
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
# In applying this license, CERN does not waive the privileges and immunities
# granted to it by virtue of its status as an Intergovernmental Organization
# or submit itself to any jurisdiction.

"""Record receivers."""

from __future__ import absolute_import, division, print_function

from itertools import chain

from flask_sqlalchemy import models_committed

from invenio_indexer.api import RecordIndexer
from invenio_indexer.signals import before_record_index
from invenio_records.models import RecordMetadata

from inspire_dojson.utils import get_recid_from_ref
from inspire_utils.helpers import force_list
from inspire_utils.record import get_value
from inspirehep.modules.records.api import InspireRecord
from inspirehep.utils.date import create_earliest_date

from .experiments import EXPERIMENTS_MAP
from .signals import after_record_enhanced


@models_committed.connect
def receive_after_model_commit(sender, changes):
    """Perform actions after models committed to database."""
    indexer = RecordIndexer()
    for model_instance, change in changes:
        if isinstance(model_instance, RecordMetadata):
            if change in ('insert', 'update'):
                indexer.index(InspireRecord(model_instance.json, model_instance))
            else:
                indexer.delete(InspireRecord(model_instance.json, model_instance))


@before_record_index.connect
def enhance_record(sender, json, *args, **kwargs):
    """Runs all the record enhancers and fires the after_record_enhanced signals
       to allow receivers work with a fully populated record."""
    populate_inspire_document_type(sender, json, *args, **kwargs)
    match_valid_experiments(sender, json, *args, **kwargs)
    populate_recid_from_ref(sender, json, *args, **kwargs)
    populate_abstract_source_suggest(sender, json, *args, **kwargs)
    populate_title_suggest(sender, json, *args, **kwargs)
    populate_affiliation_suggest(sender, json, *args, **kwargs)
    after_record_enhanced.send(json)
    add_book_autocomplete(sender, json, *args, **kwargs)


def add_book_autocomplete(sender, json, *args, **kwargs):
    if 'book' in json.get('document_type', []):
        authors = force_list(get_value(json, 'authors.full_name'))
        titles = force_list(get_value(json, 'titles.title'))

        result = json.get('bookautocomplete', [])
        result.extend(authors)
        result.extend(titles)

        ref = get_value(json, 'self.$ref')

        json.update({
            'bookautocomplete': {
                'input': result,
                'payload': {
                    'authors': authors,
                    'id': ref,
                    'title': titles,
                },
            },
        })


def populate_inspire_document_type(sender, json, *args, **kwargs):
    """Populate the INSPIRE doc type before indexing.

    Adds the `facet_inspire_doc_type` key to the record, to be used for
    faceting in the search interface.
    """
    result = []

    result.extend(json.get('document_type', []))
    result.extend(json.get('publication_type', []))
    if 'refereed' in json and json['refereed']:
        result.append('peer reviewed')

    json['facet_inspire_doc_type'] = result


def match_valid_experiments(sender, json, *args, **kwargs):
    """Normalize the experiment names before indexing.

    FIXME: this is currently using a static Python dictionary, while it should
    use the current dynamic state of the Experiments collection.
    """
    def _normalize(experiment):
        try:
            result = EXPERIMENTS_MAP[experiment.lower().replace(' ', '')]
        except KeyError:
            result = experiment

        return result

    if 'accelerator_experiments' in json:
        accelerator_exps = json['accelerator_experiments']
        for accelerator_exp in accelerator_exps:
            facet_experiment = []
            if 'experiment' in accelerator_exp:
                experiments = force_list(accelerator_exp['experiment'])
                for experiment in experiments:
                    normalized_experiment = _normalize(experiment)
                    facet_experiment.append(normalized_experiment)
                accelerator_exp['facet_experiment'] = [facet_experiment]


def populate_recid_from_ref(sender, json, *args, **kwargs):
    """Extracts recids from all reference fields and adds them to ES.

    For every field that has as a value a reference object to another record,
    add a sibling after extracting the record id.

    Example::

        {"record": {"$ref": "http://x/y/2}}

        is transformed to:

        {"record": {"$ref": "http://x/y/2},
         "recid": 2}

    Siblings are renamed using the following scheme:
        Remove "record" occurrences and append _recid without doubling or
        prepending underscores to the original name.

    For every known list of object references add a new list with the
    corresponding recids.

    Example::

        {"records": [{"$ref": "http://x/y/1"}, {"$ref": "http://x/y/2"}]}

        is transformed to:

        {"records": [{"$ref": "http://x/y/1"}, {"$ref": "http://x/y/2"}]
         "recids": [1, 2]}
    """
    list_ref_fields_translations = {
        'deleted_records': 'deleted_recids'
    }

    def _recusive_find_refs(json_root):
        if isinstance(json_root, list):
            items = enumerate(json_root)
        elif isinstance(json_root, dict):
            # Note that items have to be generated before altering the dict.
            # In this case, iteritems might break during iteration.
            items = json_root.items()
        else:
            items = []

        for key, value in items:
            if (isinstance(json_root, dict) and isinstance(value, dict) and
                    '$ref' in value):
                # Append '_recid' and remove 'record' from the key name.
                key_basename = key.replace('record', '').rstrip('_')
                new_key = '{}_recid'.format(key_basename).lstrip('_')
                json_root[new_key] = get_recid_from_ref(value)
            elif (isinstance(json_root, dict) and isinstance(value, list) and
                    key in list_ref_fields_translations):
                new_list = [get_recid_from_ref(v) for v in value]
                new_key = list_ref_fields_translations[key]
                json_root[new_key] = new_list
            else:
                _recusive_find_refs(value)

    _recusive_find_refs(json)


def populate_abstract_source_suggest(sender, json, *args, **kwargs):
    """Populate abstract_source_suggest field of HEP records."""

    # FIXME: Use a dedicated method when #1355 will be resolved.
    if 'hep.json' in json.get('$schema'):
        abstracts = json.get('abstracts', [])
        for abstract in abstracts:
            source = abstract.get('source')
            if source:
                abstract.update({
                    'abstract_source_suggest': {
                        'input': source,
                        'output': source,
                    },
                })


def populate_title_suggest(sender, json, *args, **kwargs):
    """Populate title_suggest field of Journals records."""
    if 'journals.json' in json.get('$schema'):
        journal_title = get_value(json, 'journal_title.title', default='')
        short_title = json.get('short_title', '')
        title_variants = json.get('title_variants', [])

        input_values = []
        input_values.append(journal_title)
        input_values.append(short_title)
        input_values.extend(title_variants)
        input_values = [el for el in input_values if el]

        json.update({
            'title_suggest': {
                'input': input_values,
                'output': short_title if short_title else '',
                'payload': {
                    'full_title': journal_title if journal_title else ''
                }
            }
        })


def populate_affiliation_suggest(sender, json, *args, **kwargs):
    """Populate the ``affiliation_suggest`` field of Institution records."""

    # FIXME: Use a dedicated method when #1355 will be resolved.
    if 'institutions.json' in json.get('$schema'):
        ICN = json.get('ICN', [])
        institution_acronyms = get_value(json, 'institution_hierarchy.acronym', default=[])
        institution_names = get_value(json, 'institution_hierarchy.name', default=[])
        legacy_ICN = json.get('legacy_ICN', '')
        name_variants = force_list(get_value(json, 'name_variants.value', default=[]))
        postal_codes = force_list(get_value(json, 'addresses.postal_code', default=[]))

        input_values = []
        input_values.extend(ICN)
        input_values.extend(institution_acronyms)
        input_values.extend(institution_names)
        input_values.append(legacy_ICN)
        input_values.extend(name_variants)
        input_values.extend(postal_codes)
        input_values = [el for el in input_values if el]

        json.update({
            'affiliation_suggest': {
                'input': input_values,
                'output': legacy_ICN,
                'payload': {
                    '$ref': get_value(json, 'self.$ref'),
                    'ICN': ICN,
                    'institution_acronyms': institution_acronyms,
                    'institution_names': institution_names,
                    'legacy_ICN': legacy_ICN,
                },
            },
        })


@before_record_index.connect
def earliest_date(sender, json, *args, **kwargs):
    """Find and assign the earliest date to a HEP paper."""
    date_paths = [
        'preprint_date',
        'thesis_info.date',
        'thesis_info.defense_date',
        'publication_info.year',
        'legacy_creation_date',
        'imprints.date',
    ]

    dates = list(chain.from_iterable(
        [force_list(get_value(json, path)) for path in date_paths]))

    earliest_date = create_earliest_date(dates)
    if earliest_date:
        json['earliest_date'] = earliest_date
