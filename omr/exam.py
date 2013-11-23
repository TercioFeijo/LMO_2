#Copyright (C) 2013 Greg Miller <gmill002@gmail.com>
"""single exam processing"""
import itertools
import logging
import multiprocessing 
import os
from os.path import abspath, basename, dirname, join
from pathlib import Path
import numpy as num
from PIL import Image

from omr.forms import FORMS

LOG = multiprocessing.get_logger()

def star_process_exam(args):
    """wrapper for process_exam() that takes list of args"""
    return process_exam(*args)
    
def process_exam(imfile, formstr, side):
    """Process input test image returning answer choices
    
    - initialize and configure the form
    - load image (load, check dpi, trim margins, check size)      
    - set offset using fitted image reference boxes (if defined)
    - extract answer bubble means, select and validate answer choices
    - write name image (if defined)
    - write validation image containing overlays of reference fits and bubble means
    
    """
    #initialize form with config parameters
    form = Form(config = FORMS[formstr][side])
    
    # load image (load, check dpi, trim margins, check size)
    img = form.load_image(str(imfile))
    img = form.trim_margins(img)     
    form.check_size(img)
    
    # fit image reference boxes
    if form.refzone:
        meanfit, fit = form.fit_reference(img)    
        img = form.overlay_ref_fit(img, meanfit, fit)    
        form.set_offset(*meanfit)
    
    # extract answer bubble means, choices
    bubble_means = form.get_bubble_means(img)
    choice = form.choose_answers(bubble_means)
    img = form.overlay_bubble_means(img, bubble_means)
    
    # write name image
    #name_file = Path(Path(imfile).parent, 'OMR', 'names', Path(imfile).name)
    name_file = join(dirname(imfile), 'OMR', 'names', basename(imfile)[:-3] + 'png')
    form.write_info_image(img, name_file)
    
    # write validation image 
    val_file = join(dirname(imfile), 'OMR', 'validation', basename(imfile))        
    form.write_validation(img, val_file)
    
    # write status to console 
    LOG.setLevel(logging.INFO)
    LOG.info(basename(imfile))
    LOG.setLevel(logging.WARN)
    
    return choice

class Form:
    """Represents a generic exam form. Specify answer grid size properties, reference and info rectangles 

    
    Form specification
    ------------------  
    
    Grid parameters specified as [height, width] in pixels.  
    
    ================  ===============================================================================
    Parameter         Description
    ================  ===============================================================================
    size              h,w size of answer bubble matrix (n questions, n answer choices)
    pos               h,w coordinate of answer matrix upper left corner (i0, j0) in pixels
    bub               h,w answer bubble surrounding box in pixels (float ok)         
    space             h,w unit cell edge lengths in pixels (float ok)
    offset            h,w fitted reference offset applied to pos
    ================  ===============================================================================
    
    ::
        
        |
        |        |-bub-|
        |        |---space---|
        |
        |       j0    j1    j2
        |        ___________________
        |    i0 |0                  |  
        |       | [ A ]       [ B ] |  
        |    i1 |      1            |  
        |       |                   |
        |    i2 |            2      |
        |       | [ A ]       [ B ] |
        |       |___________________|
        |


    Region rectangles
    -----------------

    rectangels are specified as [min height, max height, min width, max width] in pixels
    
    ================  ==========================================================
    Parameter         Description
    ================  ==========================================================
    refzone           list of black reference box rectangles (or None).
    info              name info rectangle (or None)
    score             machine printed score rectangle (or None)
    ================  ==========================================================
    
    Image properties 
    ----------------
    
    ================  ====================================================================
    Parameter         Description
    ================  ====================================================================
    expected_dpi      h,w image dpi (CRITICAL - relates pixels to distance)
    expected_size     h,w expected image size (after conversion to proper dpi) 
    size_tolerance    allowed percent error in actual image size 
    contrast          black/white contrast split value 0<=x<=255
    trim_std          minimum stdev to remove image edge during trimming
    radius            reference box fitting search radius in pixels
    min_ref           minimum pixel value for black box match 0<=x<=255
    ref_x, ref_y      validation image reference fit summary panel coordinates
    signal            minimum ratio of darkest to second darkest answer choice    
    ================  ====================================================================
    
    """
    size           = [0, 0]  
    offset         = [0, 0]
    pos            = [0, 0] 
    bub            = [0, 0]        
    space          = [0, 0]
    
    info           = None 
    score          = None 
    refzone        = None 
    
    expected_dpi   = [0, 0]
    expected_size  = [0, 0] 
    size_tolerance = [0, 0]
    refrc   = 0, 0 
    contrast       = 0.0 * 255 
    trim_std       = 0         
    radius         = 0         
    min_ref        = 0.0 * 255 
    signal         = 0.0       
    
    def __init__(self, config={}):
        """initialize form, calculate default coordinates.  """
        [setattr(self, k, v) for k, v in config.items()]
        self.calc_coords()

    def calc_coords(self):
        """calculate (m, n, 4) sized matrix of answer bubble
        hmin,hmax,wmin,wmax coordinates"""
        i = num.outer(num.arange(self.size[0]), num.ones(self.size[1]))
        i0 = self.pos[0] + (i * self.space[0])
        i1 = self.pos[0] + (i * self.space[0]) + self.bub[0]
        
        j = num.outer(num.ones(self.size[0]), num.arange(self.size[1]))        
        j0 = self.pos[1] + (j * self.space[1])
        j1 = self.pos[1] + (j * self.space[1]) + self.bub[1]
        
        self.coords = num.dstack((i0,i1,j0,j1)).astype('i')
    
    def set_offset(self, r=0, c=0):
        """update positional parameters with offset, recalculate
        extracted rectangles and coordinates matrix"""
        self.offset = num.array(self.offset) + num.array([r, c])
        self.pos = [self.pos[0] + r, self.pos[1] + c]

        if self.info:
            self.info = num.array(self.info) + num.array([r, r, c, c])
    
        if self.score:
            self.score = num.array(self.score) + num.array([r, r, c, c])
        
        self.calc_coords()
    
    def load_image(self, imfile):
        """open input image, correct dpi, return greyscale array"""
        im = Image.open(imfile)          # im = open as PIL image 
        
        dpi_ratio = num.true_divide(self.expected_dpi, num.array(im.info['dpi']))
        newsize = (num.array(im.size) * dpi_ratio).astype('i')
        if not all(newsize == num.array(im.size)):
            im = im.resize(newsize, Image.BICUBIC) # change dpi
        
        img = num.array(im.convert('L')) # convert to greyscale array 0-255
        
        return img

    def trim_margins(self, img):
        """Recursivly trim blank edges (low stdev) from input array"""
        oldsize = (0, 0)
        while oldsize != img.shape: # while the size is changing
            oldsize = img.shape
            for i in range(4):                         # 4 times
                img = num.rot90(img)                   #   rotate 90
                if num.std(img[0, :]) < self.trim_std: #   if low std
                    img = img[1:, :]                   #     trim edge 
    
        return img
        
    def check_size(self, img):
        """Check input image dimensions are within form tolerance. """
        absdiff = num.abs(num.subtract(img.shape, self.expected_size))
        pctdiff = num.true_divide(absdiff, self.expected_size)
        if not num.all(pctdiff <= self.size_tolerance):
            raise StandardError('image size outside form tolerance {} != {}'\
                                    .format(img.shape, self.expected_size))
        
    def fit_reference(self, img):
        """Get the best translation offset by fitting black box
        reference zones"""
        bw_img = 255 * (img >= self.contrast) 
        fit = [center_on_box(bw_img, self.radius, self.min_ref, *ref) for ref in self.refzone]
        meanfit = num.mean(num.ma.masked_array(fit, fit == -9999), axis=0).astype('i')
        if meanfit[0] is num.ma.masked:
            raise StandardError('At least one reference box match required')
    
        return meanfit, fit                  

    def get_bubble_means(self, img):
        """get the mean pixel value in each answer bubble region"""
        bw_img = 255 * (img >= self.contrast)
        means = num.zeros(self.coords.shape[:2])     
        for (i,j) in itertools.product(*map(range, self.size)):
            i0, i1, j0, j1 = self.coords[i, j, :]
            means[i, j] = num.mean(bw_img[i0:i1, j0:j1])

        return means
    
    def choose_answers(self, means):     
        """choose darkest answer choice. assign poor signal choices -1"""
        choice = num.argmin(means, axis=1)
        if self.signal:
            sorted_rows = num.sort(means, axis=1)
            signal = sorted_rows[:,1] / sorted_rows[:,0]
            choice[signal <= self.signal] = -1
            
        return choice

    def overlay_ref_fit(self, img, mean, fit, off=25):
        """draw crosses at the corners of the initial and fitted
        reference boxes"""
        def plus(img, x, y, val=0, r=10):
            img[x-1:x, y-r:y+r], img[x-r:x+r, y-1:y] = val, val
            return img
        
        if len(self.refzone) != 4:
            return img
                        
        centers = [(self.ref_rc[0] - off, self.ref_rc[1] - off), 
                   (self.ref_rc[0] - off, self.ref_rc[1] + off), 
                   (self.ref_rc[0] + off, self.ref_rc[1] - off), 
                   (self.ref_rc[0] + off, self.ref_rc[1] + off)]
                           
        img = plus(img, self.ref_rc[0], self.ref_rc[1], val=150, r=15) # final mean offset
        img = plus(img, self.ref_rc[0] + mean[0], self.ref_rc[1] + mean[1], val=0)
        for [x0,x1,y0,y1], [x_off, y_off], (cx, cy) in zip(self.refzone, fit, centers):
            img = plus(img, cx,  cy, val=120, r=15)        # panel fitted
            img = plus(img, cx + x_off, cy + y_off, val=0) # panel reference            
            img = plus(img, x0, y0, val=150)               # expected reference
            img = plus(img, x1, y1, val=150)               #
            img = plus(img, x0 + x_off, y0 + y_off, val=0) # actual fitted
            img = plus(img, x1 + x_off, y1 + y_off, val=0) # 
        
        return img
    
    def overlay_bubble_means(self, img, means):
        """overlay the bubble region mean values onto the image"""
        for (i,j) in itertools.product(*map(range, self.size)):
            i0, i1, j0, j1 = self.coords[i, j, :]
            img[i0:i1, j0:j1] = means[i,j]
    
        return img
    
    def write_validation(self, img, val_file):
        """write the greyscale image that has been marked with reference
        fits and bubble means"""
        Image.fromarray(img).save(val_file)

    def write_info_image(self, img, name_file):
        """extract the forms info box region and stack the score box"""
        if self.info not in [[], None]:
            xmin, xmax, ymin, ymax = self.info
            nameimg = num.rot90(img[xmin:xmax, ymin:ymax])
    
            if self.score not in [[], None]:
                xmin, xmax, ymin, ymax = self.score
                score = num.rot90(img[xmin:xmax, ymin:ymax])
                nameimg = num.hstack([nameimg[30:75, :], score])

            Image.fromarray(nameimg).save(name_file)


def center_on_box(img, radius, min_ref, xmin, xmax, ymin, ymax, na_val=-9999):
    """find the best offset for a black box by trying all within a
    circular search radius"""
    x, y = num.meshgrid(num.arange(-radius, radius), num.arange(-radius, radius))
    coords = [(i, j) for i, j in zip(x.flatten(), y.flatten()) if (i**2 + j**2)**0.5 <= radius]    
    fit = [num.mean(img[(xmin+i):(xmax+i), (ymin+j):(ymax+j)]) for i, j in coords]
    if num.nanmin(fit) <= min_ref:
        return num.array(coords[num.nanargmin(fit)])
    else:
        return num.array([na_val, na_val]) 
