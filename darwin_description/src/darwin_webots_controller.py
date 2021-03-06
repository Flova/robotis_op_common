import subprocess
import time

import tf
from controller import Robot, Node, Supervisor, Field
import rospy
from sensor_msgs.msg import JointState, Imu, Image
from rosgraph_msgs.msg import Clock

from bitbots_msgs.msg import JointCommand
import math
import os

G = 9.8


class DarwinController:
    def __init__(self, namespace='', node=True):
        self.time = 0
        self.clock_msg = Clock()
        self.namespace = namespace
        self.supervisor = Supervisor()

        self.motor_names = ["ShoulderR", "ShoulderL", "ArmUpperR", "ArmUpperL", "ArmLowerR", "ArmLowerL",
                            "PelvYR", "PelvYL", "PelvR", "PelvL", "LegUpperR", "LegUpperL", "LegLowerR", "LegLowerL",
                            "AnkleR", "AnkleL", "FootR", "FootL",
                            "Neck", "Head"]
        self.walkready = [0] * 20
        self.names_webots_to_bitbots = {"ShoulderR": "RShoulderPitch",
                                        "ShoulderL": "LShoulderPitch",
                                        "ArmUpperR": "RShoulderRoll",
                                        "ArmUpperL": "LShoulderRoll",
                                        "ArmLowerR": "RElbow",
                                        "ArmLowerL": "LElbow",
                                        "PelvYR": "RHipYaw",
                                        "PelvYL": "LHipYaw",
                                        "PelvR": "RHipRoll",
                                        "PelvL": "LHipRoll",
                                        "LegUpperR": "RHipPitch",
                                        "LegUpperL": "LHipPitch",
                                        "LegLowerR": "RKnee",
                                        "LegLowerL": "LKnee",
                                        "AnkleR": "RAnklePitch",
                                        "AnkleL": "LAnklePitch",
                                        "FootR": "RAnkleRoll",
                                        "FootL": "LAnkleRoll",
                                        "Neck": "HeadPan",
                                        "Head": "HeadTilt"}
        self.names_bitbots_to_webots = {"RShoulderPitch": "ShoulderR",
                                        "LShoulderPitch": "ShoulderL",
                                        "RShoulderRoll": "ArmUpperR",
                                        "LShoulderRoll": "ArmUpperL",
                                        "RElbow": "ArmLowerR",
                                        "LElbow": "ArmLowerL",
                                        "RHipYaw": "PelvYR",
                                        "LHipYaw": "PelvYL",
                                        "RHipRoll": "PelvR",
                                        "LHipRoll": "PelvL",
                                        "RHipPitch": "LegUpperR",
                                        "LHipPitch": "LegUpperL",
                                        "RKnee": "LegLowerR",
                                        "LKnee": "LegLowerL",
                                        "RAnklePitch": "AnkleR",
                                        "LAnklePitch": "AnkleL",
                                        "RAnkleRoll": "FootR",
                                        "LAnkleRoll": "FootL",
                                        "HeadPan": "Neck",
                                        "HeadTilt": "Head"}

        self.motors = []
        self.sensors = []
        self.timestep = int(self.supervisor.getBasicTimeStep())
        self.timestep = 10

        for motor_name in self.motor_names:
            self.motors.append(self.supervisor.getMotor(motor_name))
            self.motors[-1].enableTorqueFeedback(self.timestep)
            self.sensors.append(self.supervisor.getPositionSensor(motor_name + "S"))
            self.sensors[-1].enable(self.timestep)

        self.accel = self.supervisor.getAccelerometer("Accelerometer")
        self.accel.enable(self.timestep)
        self.gyro = self.supervisor.getGyro("Gyro")
        self.gyro.enable(self.timestep)
        self.camera = self.supervisor.getCamera("Camera")
        self.camera.enable(self.timestep)

        if node:
            rospy.init_node("webots_darwin_ros_interface", anonymous=True,
                            argv=['clock:=/' + self.namespace + '/clock'])
        self.pub_js = rospy.Publisher(self.namespace + "/joint_states", JointState, queue_size=1)
        self.pub_imu = rospy.Publisher(self.namespace + "/imu/data", Imu, queue_size=1)
        self.pub_cam = rospy.Publisher(self.namespace + "/image_raw", Image, queue_size=1)
        self.clock_publisher = rospy.Publisher(self.namespace + "/clock", Clock, queue_size=1)
        rospy.Subscriber(self.namespace + "/DynamixelController/command", JointCommand, self.command_cb)

        self.world_info = self.supervisor.getFromDef("world_info")
        self.hinge_joint = self.supervisor.getFromDef("barrier_hinge")

        self.robot_node = self.supervisor.getFromDef("Darwin")
        self.translation_field = self.robot_node.getField("translation")
        self.rotation_field = self.robot_node.getField("rotation")

    def step_sim(self):
        self.supervisor.step(self.timestep)

    def step(self):
        self.step_sim()
        self.time += self.timestep / 1000
        self.publish_imu()
        self.publish_joint_states()
        self.clock_msg.clock = rospy.Time.from_seconds(self.time)
        self.clock_publisher.publish(self.clock_msg)

    def command_cb(self, command: JointCommand):
        for i, name in enumerate(command.joint_names):
            try:
                motor_index = self.motor_names.index(self.names_bitbots_to_webots[name])
                self.motors[motor_index].setPosition(command.positions[i])
            except ValueError:
                print(f"invalid motor specified ({self.names_bitbots_to_webots[name]})")
        self.publish_joint_states()
        self.publish_imu()
        self.publish_camera()

    def publish_joint_states(self):
        js = JointState()
        js.name = []
        js.header.stamp = rospy.get_rostime()
        js.position = []
        js.effort = []
        for i in range(len(self.sensors)):
            js.name.append(self.names_webots_to_bitbots[self.motor_names[i]])
            value = self.sensors[i].getValue()
            js.position.append(value)
            js.effort.append(self.motors[i].getTorqueFeedback())
        self.pub_js.publish(js)

    def publish_imu(self):
        msg = Imu()
        msg.header.stamp = rospy.get_rostime()
        msg.header.frame_id = "imu_frame"
        accel_vels = self.accel.getValues()

        msg.linear_acceleration.x = ((accel_vels[0] - 512.0) / 512.0) * 3 * G
        msg.linear_acceleration.y = ((accel_vels[1] - 512.0) / 512.0) * 3 * G
        msg.linear_acceleration.z = ((accel_vels[2] - 512.0) / 512.0) * 3 * G
        gyro_vels = self.gyro.getValues()
        msg.angular_velocity.x = ((gyro_vels[0] - 512.0) / 512.0) * 1600 * (
                math.pi / 180)  # is 400 deg/s the real value
        msg.angular_velocity.y = ((gyro_vels[1] - 512.0) / 512.0) * 1600 * (math.pi / 180)
        msg.angular_velocity.z = ((gyro_vels[2] - 512.0) / 512.0) * 1600 * (math.pi / 180)
        self.pub_imu.publish(msg)

    def publish_camera(self):
        img_msg = Image()
        img_msg.header.stamp = rospy.get_rostime()
        img_msg.height = self.camera.getHeight()
        img_msg.width = self.camera.getWidth()
        img_msg.encoding = "bgra8"
        img_msg.step = 4 * self.camera.getWidth()
        img = self.camera.getImage()
        img_msg.data = img
        self.pub_cam.publish(img_msg)

    def set_gravity(self, active):
        if active:
            self.world_info.getField("gravity").setSFVec3f([0.0, -9.81, 0.0])
        else:
            self.world_info.getField("gravity").setSFVec3f([0.0, 0.0, 0.0])

    def reset_robot_pose(self, pos, quat):
        rpy = tf.transformations.euler_from_quaternion(quat)
        self.set_robot_pose_rpy(pos, rpy)
        self.robot_node.resetPhysics()

    def reset_robot_pose_rpy(self, pos, rpy):
        self.set_robot_pose_rpy(pos, rpy)
        self.robot_node.resetPhysics()

    def reset(self):
        self.supervisor.simulationReset()

    def node(self):
        s = self.supervisor.getSelected()
        if s is not None:
            print(f"id: {s.getId()}, type: {s.getType()}, def: {s.getDef()}")

    def set_robot_pose_rpy(self, pos, rpy):
        self.translation_field.setSFVec3f(pos_ros_to_webots(pos))
        self.rotation_field.setSFRotation(rpy_to_axis(*rpy))

    def get_robot_pose_rpy(self):
        pos = self.translation_field.getSFVec3f()
        rot = self.rotation_field.getSFRotation()
        return pos_webots_to_ros(pos), axis_to_rpy(*rot)


def pos_webots_to_ros(pos):
    x = pos[2]
    y = pos[0]
    z = pos[1]
    return [x, y, z]


def pos_ros_to_webots(pos):
    z = pos[0]
    x = pos[1]
    y = pos[2]
    return [x, y, z]


def rpy_to_axis(z_e, x_e, y_e, normalize=True):
    # Assuming the angles are in radians.
    c1 = math.cos(z_e / 2)
    s1 = math.sin(z_e / 2)
    c2 = math.cos(x_e / 2)
    s2 = math.sin(x_e / 2)
    c3 = math.cos(y_e / 2)
    s3 = math.sin(y_e / 2)
    c1c2 = c1 * c2
    s1s2 = s1 * s2
    w = c1c2 * c3 - s1s2 * s3
    x = c1c2 * s3 + s1s2 * c3
    y = s1 * c2 * c3 + c1 * s2 * s3
    z = c1 * s2 * c3 - s1 * c2 * s3
    angle = 2 * math.acos(w)
    if normalize:
        norm = x * x + y * y + z * z
        if norm < 0.001:
            # when all euler angles are zero angle =0 so
            # we can set axis to anything to avoid divide by zero
            x = 1
            y = 0
            z = 0
        else:
            norm = math.sqrt(norm)
            x /= norm
            y /= norm
            z /= norm
    return [z, x, y, angle]


def axis_to_rpy(x, y, z, angle):
    s = math.sin(angle)
    c = math.cos(angle)
    t = 1 - c

    magnitude = math.sqrt(x * x + y * y + z * z)
    if magnitude == 0:
        raise AssertionError
    x /= magnitude
    y /= magnitude
    z /= magnitude
    # north pole singularity
    if (x * y * t + z * s) > 0.998:
        yaw = 2 * math.atan2(x * math.sin(angle / 2), math.cos(angle / 2))
        pitch = math.pi / 2
        roll = 0
        return roll, pitch, yaw

    # south pole singularity
    if (x * y * t + z * s) < -0.998:
        yaw = -2 * math.atan2(x * math.sin(angle / 2), math.cos(angle / 2))
        pitch = -math.pi / 2
        roll = 0
        return roll, pitch, yaw

    yaw = math.atan2(y * s - x * z * t, 1 - (y * y + z * z) * t)
    pitch = math.asin(x * y * t + z * s)
    roll = math.atan2(x * s - y * z * t, 1 - (x * x + z * z) * t)

    return roll, pitch, yaw