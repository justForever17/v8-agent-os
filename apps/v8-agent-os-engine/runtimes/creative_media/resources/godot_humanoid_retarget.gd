extends SceneTree

const ORDER := [
    "hips", "spine", "chest", "upper_chest", "neck", "head",
    "left_shoulder", "left_upper_arm", "left_lower_arm", "left_hand",
    "right_shoulder", "right_upper_arm", "right_lower_arm", "right_hand",
    "left_upper_leg", "left_lower_leg", "left_foot", "left_toes",
    "right_upper_leg", "right_lower_leg", "right_foot", "right_toes",
]

func _fail(message: String) -> void:
    push_error(message)
    quit(1)

func _find_skeleton(node: Node) -> Skeleton3D:
    if node is Skeleton3D:
        return node as Skeleton3D
    for child in node.get_children():
        var found := _find_skeleton(child)
        if found != null:
            return found
    return null

func _bone_direction(skeleton: Skeleton3D, bone_index: int) -> Vector3:
    var children := skeleton.get_bone_children(bone_index)
    if children.is_empty():
        return Vector3.ZERO
    var origin := skeleton.get_bone_global_rest(bone_index).origin
    var target := skeleton.get_bone_global_rest(int(children[0])).origin
    return (target - origin).normalized()

func _initialize() -> void:
    var args := OS.get_cmdline_user_args()
    if args.size() != 3:
        _fail("V8 retarget requires model, motion JSON, and output paths")
        return
    var model_path := String(args[0])
    var motion_path := String(args[1])
    var output_path := String(args[2])
    var motion = JSON.parse_string(FileAccess.get_file_as_string(motion_path))
    if not motion is Dictionary:
        _fail("V8 motion JSON is invalid")
        return
    var document := GLTFDocument.new()
    var state := GLTFState.new()
    var import_error := document.append_from_file(model_path, state)
    if import_error != OK:
        _fail("Godot could not import target glTF: %s" % error_string(import_error))
        return
    var root := document.generate_scene(state, 30.0, false, false)
    if root == null:
        _fail("Godot did not generate a target scene")
        return
    get_root().add_child(root)
    var skeleton := _find_skeleton(root)
    if skeleton == null:
        _fail("Target scene has no Skeleton3D")
        return
    var times: Array = motion.get("times", [])
    var mapping: Dictionary = motion.get("mapping", {})
    var directions: Dictionary = motion.get("directions", {})
    if times.is_empty():
        _fail("V8 motion timeline is empty")
        return
    var rotations := {}
    for canonical in ORDER:
        if mapping.has(canonical):
            rotations[canonical] = []
    for frame_index in range(times.size()):
        skeleton.reset_bone_poses()
        for canonical in ORDER:
            if not mapping.has(canonical) or not directions.has(canonical):
                continue
            var samples: Array = directions[canonical]
            if frame_index >= samples.size() or samples[frame_index] == null:
                rotations[canonical].append(null)
                continue
            var bone_index := skeleton.find_bone(StringName(mapping[canonical]))
            if bone_index < 0:
                rotations[canonical].append(null)
                continue
            var rest_direction := _bone_direction(skeleton, bone_index)
            var sample: Array = samples[frame_index]
            var target_direction := Vector3(float(sample[0]), float(sample[1]), float(sample[2])).normalized()
            if rest_direction.is_zero_approx() or target_direction.is_zero_approx():
                rotations[canonical].append(null)
                continue
            var rest_global := skeleton.get_bone_global_rest(bone_index)
            var delta := Quaternion(rest_direction, target_direction)
            skeleton.set_bone_global_pose(
                bone_index,
                Transform3D(Basis(delta) * rest_global.basis, rest_global.origin)
            )
            rotations[canonical].append(skeleton.get_bone_pose_rotation(bone_index))
    var animation := Animation.new()
    animation.length = max(0.001, float(times[times.size() - 1]))
    animation.loop_mode = Animation.LOOP_NONE
    for canonical in ORDER:
        if not rotations.has(canonical):
            continue
        var bone_name := String(mapping[canonical])
        var bone_index := skeleton.find_bone(StringName(bone_name))
        if bone_index < 0:
            continue
        var track := animation.add_track(Animation.TYPE_ROTATION_3D)
        animation.track_set_path(track, NodePath(String(root.get_path_to(skeleton)) + ":" + bone_name))
        var samples: Array = rotations[canonical]
        for frame_index in range(samples.size()):
            if samples[frame_index] != null:
                animation.rotation_track_insert_key(track, float(times[frame_index]), samples[frame_index])
    var player := AnimationPlayer.new()
    player.name = "V8MotionPlayer"
    root.add_child(player)
    player.owner = root
    var library := AnimationLibrary.new()
    library.add_animation("v8_motion", animation)
    player.add_animation_library("v8", library)
    var export_document := GLTFDocument.new()
    var export_state := GLTFState.new()
    var append_error := export_document.append_from_scene(root, export_state)
    if append_error != OK:
        _fail("Godot could not stage retargeted scene: %s" % error_string(append_error))
        return
    var write_error := export_document.write_to_filesystem(export_state, output_path)
    if write_error != OK:
        _fail("Godot could not export retargeted GLB: %s" % error_string(write_error))
        return
    print("V8_RETARGET_OK")
    quit(0)
