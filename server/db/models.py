import datetime

from flask import request
from werkzeug.exceptions import BadRequest

from server.db.db import db, RemoteAccount, Aup, EmailVerification, Iuid

deserialization_mapping = {"remote_accounts": RemoteAccount,
                           "aups": Aup,
                           "email_verifications": EmailVerification,
                           "iuids": Iuid}

forbidden_fields = ["created_at", "modified_at"]
date_fields = ["created_at", "modified_at"]


def flatten(l):
    return [item for sublist in l for item in sublist]


def validate(cls, json_dict, is_new_instance=True):
    required_columns = {k: v for k, v in cls.__table__.columns._data.items() if
                        not v.nullable and (not v.server_default or v.primary_key)}
    required_attributes = required_columns.keys()
    if is_new_instance:
        required_attributes = filter(lambda k: not required_columns[k].primary_key, required_attributes)
    missing_attributes = list(filter(lambda key: key not in json_dict, required_attributes))
    if len(missing_attributes) > 0:
        raise BadRequest(f"Missing attributes '{', '.join(missing_attributes)}' for {cls.__name__}")


def _merge(cls, d):
    o = cls(**d)
    merged = db.session.merge(o)
    db.session.commit()
    return merged


def save(cls, custom_json=None, allow_child_cascades=True):
    if not request.is_json and custom_json is None:
        return None, 415

    json_dict = request.get_json() if custom_json is None else custom_json

    json_dict = transform_json(cls, json_dict, allow_child_cascades=allow_child_cascades)

    validate(cls, json_dict)
    return _merge(cls, json_dict), 201


def update(cls, custom_json=None, allow_child_cascades=True):
    if not request.is_json and custom_json is None:
        return None, 415

    json_dict = request.get_json() if custom_json is None else custom_json
    json_dict = transform_json(cls, json_dict, allow_child_cascades=allow_child_cascades)

    pk = list({k: v for k, v in cls.__table__.columns._data.items() if v.primary_key}.keys())[0]

    # This will raise a NoResultFound and result in a 404
    cls.query.filter(cls.__dict__[pk] == json_dict[pk]).one()
    validate(cls, json_dict, is_new_instance=False)
    return _merge(cls, json_dict), 201


def delete(cls, primary_key):
    pk = list({k: v for k, v in cls.__table__.columns._data.items() if v.primary_key}.keys())[0]
    row_count = cls.query.filter(cls.__dict__[pk] == primary_key).delete()
    db.session.commit()
    return (None, 204) if row_count > 0 else (None, 404)


def cleanse_json(json_dict, cls=None, allow_child_cascades=True):
    if cls:
        column_names = cls.__table__.columns._data.keys()
        if allow_child_cascades:
            column_names += list(cls.__dict__.keys())
        # Need to avoid RuntimeError: dictionary changed size during iteration
        for k in list(json_dict.keys()):
            if k not in column_names:
                del json_dict[k]

    for forbidden in forbidden_fields:
        if forbidden in json_dict:
            del json_dict[forbidden]
        for rel in flatten(filter(lambda i: isinstance(i, list), json_dict.values())):
            cleanse_json(rel, allow_child_cascades=allow_child_cascades)


def parse_date_fields(json_dict):
    for date_field in date_fields:
        if date_field in json_dict:
            val = json_dict[date_field]
            if isinstance(val, float) or isinstance(val, int):
                json_dict[date_field] = datetime.datetime.fromtimestamp(val / 1e3)
        for rel in flatten(filter(lambda i: isinstance(i, list), json_dict.values())):
            parse_date_fields(rel)


def transform_json(cls, json_dict, allow_child_cascades=True):
    def _contains_list(coll):
        return len(list(filter(lambda item: isinstance(item, list), coll))) > 0

    def _do_transform(items):
        return dict(map(_parse, items))

    def _parse(item):
        if isinstance(item[1], list):
            cls = deserialization_mapping[item[0]]
            return item[0], list(map(lambda i: cls(**_do_transform(i.items())), item[1]))
        return item

    cleanse_json(json_dict, cls=cls, allow_child_cascades=allow_child_cascades)
    parse_date_fields(json_dict)

    if _contains_list(json_dict.values()):
        return _do_transform(json_dict.items())

    return json_dict