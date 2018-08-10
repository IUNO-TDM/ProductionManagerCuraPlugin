# Copyright (c) 2017 Ultimaker B.V.
# This example is released under the terms of the AGPLv3 or higher.

import os.path #To get a file name to write to.
import requests
import time

from io import BytesIO

from UM.Application import Application #To find the scene to get the current g-code to write.
from UM.Job import Job
from UM.Logger import Logger
from UM.OutputDevice.OutputDevice import OutputDevice #An interface to implement.
from UM.OutputDevice.OutputDeviceError import WriteRequestFailedError #For when something goes wrong.
from UM.OutputDevice.OutputDevicePlugin import OutputDevicePlugin #The class we need to extend.

from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf

UFPMIMETYPE = "application/x-ufp"

class ProductionManagerDevicePlugin(OutputDevicePlugin): #We need to be an OutputDevicePlugin for the plug-in system.
    ##  Called upon launch.
    #
    #   You can use this to make a connection to the device or service, and
    #   register the output device to be displayed to the user.
    def start(self):
        self.zeroconf = Zeroconf()
        self.iunoAvailable = False
        self.browser = ServiceBrowser(self.zeroconf, "_http._tcp.local.", handlers=[self.on_service_state_change])
        self.getOutputDeviceManager().addOutputDevice(ProductionManager()) #Since this class is also an output device, we can just register ourselves.

    ##  Called upon closing.
    #
    #   You can use this to break the connection with the device or service, and
    #   you should unregister the output device to be displayed to the user.
    def stop(self):
        self.zeroconf.close()
        if self.iunoAvailable:
            self.getOutputDeviceManager().removeOutputDevice("IunoProductionManager")

    ## Called on mdns state change
    #
    def on_service_state_change(self, zeroconf, service_type, name, state_change):
        Logger.log ("d", "Service %s of type %s state changed: %s" % (name, service_type, state_change))

        if name == "IUNO Production Manager._http._tcp.local.":
            if state_change is ServiceStateChange.Added:
                if not self.iunoAvailable:
                    self.getOutputDeviceManager().addOutputDevice(ProductionManager()) #Since this class is also an output device, we can just register ourselves.
                Logger.log ("d", "Hello IUNO!")
                self.iunoAvailable = True

            elif state_change is ServiceStateChange.Removed:
                if self.iunoAvailable:
                    self.getOutputDeviceManager().removeOutputDevice("IunoProductionManager") #Remove all devices that were added. In this case it's only one.
                Logger.log ("d", "Goodbye IUNO!")
                self.iunoAvailable = False

            else:
                pass

class ProductionManager(OutputDevice): #We need an actual device to do the writing.
    def __init__(self):
        super().__init__("IunoProductionManager") #Give an ID which is used to refer to the output device.

        #Optionally set some metadata.
        self.setName("IUNO Production Manager Output Device") #Human-readable name (you may want to internationalise this). Gets put in messages and such.
        self.setShortDescription("send to IUNO") #This is put on the save button.
        self.setDescription("IUNO Production Manager")
        self.setIconName("save")

    ##  Called when the user clicks on the button to save to this device.
    #
    #   The primary function of this should be to select the correct file writer
    #   and file format to write to.
    #
    #   \param nodes A list of scene nodes to write to the file. This may be one
    #   or multiple nodes. For instance, if the user selects a couple of nodes
    #   to write it may have only those nodes. If the user wants the entire
    #   scene to be written, it will be the root node. For the most part this is
    #   not your concern, just pass this to the correct file writer.
    #   \param file_name A name for the print job, if available. If no such name
    #   is available but you still need a name in the device, your plug-in is
    #   expected to come up with a name. You could try `uuid.uuid4()`.
    #   \param limit_mimetypes Limit the possible MIME types to use to serialise
    #   the data. If None, no limits are imposed.
    #   \param file_handler What file handler to get the mesh from.
    #   \kwargs Some extra parameters may be passed here if other plug-ins know
    #   for certain that they are talking to your plug-in, not to some other
    #   output device.
    def requestWrite(self, nodes, file_name = None, limit_mimetypes = None, file_handler = None, **kwargs):
        #The file handler is an object that provides serialisation of file types.
        #There's several types of files. If not provided, it is assumed that we want to save meshes.
        if not file_handler:
            file_handler = Application.getInstance().getMeshFileHandler()

        file_types = file_handler.getSupportedFileTypesWrite()
        if not file_types:
            Logger.log("e", "No supported file types for writing.")
        else:
            Logger.log ("i", file_types)

        file_type = {}
        for ft in file_types:
            if (UFPMIMETYPE == ft["mime_type"]):
                file_type = ft
                break

        assert UFPMIMETYPE == file_type["mime_type"], "File Writer for application/x-ufp not found!"

        ufp_writer = file_handler.getWriterByMimeType(file_type["mime_type"]) #This is the object that will serialize our file for us.
        if not ufp_writer:
            raise WriteRequestFailedError("Can't find any file writer for the file type {file_type}.".format(file_type = file_type))

        ufp_writer._createSnapshot()

        job = CreateUfpAndPostJob(ufp_writer, nodes, 2) #We'll create a WriteFileJob, which gets run asynchronously in the background.

        job.progress.connect(self._onProgress) #You can listen to the event for when it's done and when it's progressing.
        job.finished.connect(self._onFinished) #This way we can properly close the file stream.

        job.start()

    def _onProgress(self, job, progress):
        Logger.log("d", "Saving file... {progress}%".format(progress = progress))

    def _onFinished(self, job):
#        job.getStream().close() #Don't forget to close the stream after it's finished.
        Logger.log("d", "Done saving file!")

class CreateUfpAndPostJob(Job):
    def __init__(self, writer, data, mode):
        super().__init__()
        self._stream = BytesIO()
        self._writer = writer
        self._data = data
        self._file_name = ""
        self._mode = mode
        self._message = None
        self.progress.connect(self._onProgress)
        self.finished.connect(self._onFinished)

    def _onFinished(self, job):
        if self == job and self._message is not None:
            self._message.hide()
            self._message = None

    def _onProgress(self, job, amount):
        if self == job and self._message:
            self._message.setProgress(amount)

    def run(self):
        Job.yieldThread()
        begin_time = time.time()
        self.setResult(self._writer.write(self._stream, self._data))
        if not self.getResult():
            self.setError(self._writer.getInformation())

        url='http://192.168.178.22:3042/api/localobjects'

        data = {'title':(None, "Foo Bar"), 'file':('foobar.ufp', self._stream)}
        resp = requests.post(url, files=data)
        if 201 != resp.status_code:
            self.setResult(False)
            self.setError(resp.text)
        Logger.log("d", "http response code %d", resp.status_code)
        end_time = time.time()
        Logger.log("d", "Writing file took %s seconds", end_time - begin_time)
