from collections import Counter

import numpy as np
import pickle
import pytest
from motpy import ModelPreset
from motpy.core import Detection, setup_logger
from motpy.testing import data_generator
from motpy.tracker import (IOUAndFeatureMatchingFunction, MultiObjectTracker,
                           EMA, match_by_cost_matrix)
from numpy.testing import assert_almost_equal, assert_array_equal

logger = setup_logger(__name__)


USE_SIMPLE_TRACKER = -1


@pytest.mark.parametrize("num_objects", [2, 5])
@pytest.mark.parametrize("order_pos", [USE_SIMPLE_TRACKER, 0, 1, 2])
@pytest.mark.parametrize("feature_similarity_beta", [None, 0.5])
def test_simple_tracking_objects(
        num_objects: int,
        order_pos: int,
        feature_similarity_beta: float,
        fps: int = 24,
        num_steps: int = 240):

    gen = data_generator(
        num_steps=num_steps,
        miss_prob=0.2,
        det_err_sigma=3.0,
        num_objects=num_objects,
        max_omega=0.01)

    dt = 1 / fps
    num_steps_warmup = 1.0 * fps  # 1 second of warmup

    if order_pos == USE_SIMPLE_TRACKER:
        model_spec = None
    else:
        model_spec = {'order_pos': order_pos, 'dim_pos': 2, 'order_size': 0, 'dim_size': 2}

    matching_fn = IOUAndFeatureMatchingFunction(feature_similarity_beta=feature_similarity_beta)
    mot = MultiObjectTracker(
        dt=dt,
        model_spec=model_spec,
        matching_fn=matching_fn)
    history = {idx: [] for idx in range(num_objects)}
    for i in range(num_steps):
        dets_gt, dets_pred = next(gen)
        detections = [d for d in dets_pred if d.box is not None]
        _ = mot.step(detections=detections)

        if i <= num_steps_warmup:
            continue

        matches = match_by_cost_matrix(mot.trackers, dets_gt)
        for m in matches:
            gidx, tidx = m[0], m[1]
            track_id = mot.trackers[tidx].id
            history[gidx].append(track_id)

        assert len(mot.trackers) == num_objects

    count_frames = (num_steps - num_steps_warmup)
    for gid in range(num_objects):
        c = Counter(history[gid])
        count_tracked = c.most_common(1)[0][1]
        logger.info('object %d mostly tracked in %.1f%% frames' %
                    (gid, (100.0 * count_tracked / count_frames)))
        assert count_tracked > 0.95 * count_frames


def test_tracker_diverges():
    box = np.array([0, 0, 10, 10])

    mot = MultiObjectTracker(dt=0.1)
    mot.step([Detection(box=box)])
    assert len(mot.trackers) == 1
    first_track_id = mot.active_tracks()[0].id

    # check if dt is propagated to single object tracker
    assert_almost_equal(mot.trackers[0].model.dt, 0.1)

    # check valid tracker
    assert not mot.trackers[0].is_invalid()
    mot.trackers[0]._tracker.x[2] = np.nan
    assert mot.trackers[0].is_invalid()

    mot.cleanup_trackers()
    assert len(mot.trackers) == 0

    # pass invalid box
    mot.step([Detection(box=box)])
    assert len(mot.trackers) == 1
    assert mot.active_tracks()[0].id != first_track_id

def test_tracker_det_indices():
    mot = MultiObjectTracker(dt=5)
    box0 = np.array([0, 0, 10, 10])
    box1 = np.array([20, 20, 30, 30])
    mot.step([Detection(box=box) for box in [box0, box1]])
    track_ids = [t.id for t in mot.active_tracks()]
    assert len(track_ids) == 2
    _, indices = mot.active_tracks(return_indices=True)
    assert indices == [0, 1]
    mot.step([Detection(box=box) for box in [box1, box0]])
    assert track_ids == [t.id for t in mot.active_tracks()]
    track_ids_idx, indices = mot.active_tracks(return_indices=True)
    assert track_ids == [t.id for t in track_ids_idx]
    assert indices == [1, 0]
    mot.step([Detection(box=box) for box in []])
    _, indices = mot.active_tracks(return_indices=True)
    assert indices == [-1, -1]

def test_class_smoothing():
    box = np.array([0, 0, 10, 10])
    mot = MultiObjectTracker(dt=0.1)
    mot.step([Detection(box=box, class_id=1)])
    mot.step([Detection(box=box, class_id=2)])
    mot.step([Detection(box=box, class_id=2)])
    assert mot.trackers[0].class_id == 2
    mot.step([Detection(box=box, class_id=1)])
    mot.step([Detection(box=box, class_id=1)])
    assert mot.trackers[0].class_id == 1

def test_pickable():
    box = np.array([0, 0, 10, 10])
    mot = MultiObjectTracker(dt=0.1)
    dumped_empty = pickle.dumps(mot)
    assert len(dumped_empty) 
    mot.step([Detection(box=box, class_id=1)])
    tracks = mot.active_tracks()
    dumped_nonempty = pickle.dumps(mot)
    assert len(dumped_nonempty)
    mot2 = pickle.loads(dumped_nonempty)
    tracks2 = mot2.active_tracks()
    assert len(tracks) == len(tracks2)
    assert tracks[0].id == tracks2[0].id
    assert np.array_equal(tracks[0].box, tracks2[0].box)
    assert tracks[0].score == tracks2[0].score
    assert tracks[0].class_id == tracks2[0].class_id
    

def test_exponential_moving_average():
    update_fn = EMA(0.5).exponential_moving_average_fn

    # scalars
    assert update_fn(None, 100.) == 100.
    assert update_fn(50., None) == 50.
    assert update_fn(50., 100.) == 75.

    # sequences
    assert_array_equal(update_fn(None, [100., 50]), [100., 50])
    assert_array_equal(update_fn([80., 50], None), [80., 50])
    assert_array_equal(update_fn(np.array([80., 100]), [90, 90]), [85., 95])
