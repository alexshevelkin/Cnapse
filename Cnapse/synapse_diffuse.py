"""
Synapse partner assignment via neurotransmitter diffusion simulation
"""

import argparse
from sys import argv

import numpy as np
import pandas as pd
import tifffile as tif
from skimage.morphology import dilation, ball

from cloudvolume import CloudVolume

from utils import load_volume

## Fibonacci spiral model
def fibonacci_spiral_sphere(n_points):

 	vec = np.zeros((n_points, 3))
 	gr = (np.sqrt(5) + 1)/2 # Golden ratio = 1.6180...

 	p = np.arange(n_points)

 	u = 2*np.pi*gr*p
 	v = 1 - (2*p + 1)/n_points
 	sinTheta = np.sqrt(1 - v**2)

 	vec[:,0] = np.cos(u)*sinTheta
 	vec[:,1] = np.sin(u)*sinTheta
 	vec[:,2] = v

 	return vec


if __name__ == "__main__":

	parser = argparse.ArgumentParser()
	parser.add_argument("--syn_seg", required=True, type=str,
		help="Synapse segmentation volume")
	parser.add_argument("--cell_seg", required=True, type=str,
		help="Cell segmentation volume")
	parser.add_argument("--mip", required=True, type=int,
		help="Mip level number (2^[mip] nm)")
	parser.add_argument("--syn_info", required=True, type=str,
		help="Synapse info")
	parser.add_argument("--outpath", required=False, type=str,
		default="synapse_assigned.csv", help="Output synapse file path")

	opt = parser.parse_args()

	syn_seg_path = opt.syn_seg
	seg_path = opt.cell_seg

	mip = opt.mip
	res = np.array((2^mip, 2^mip, 50))
	
	# Load volumes
	syn_seg = load_volume(syn_seg_path, res)
	seg = load_volume(seg_path, res)

	volsize = np.array(syn_seg.shape)

	opt.diffusionsteplength_nm = 4
	opt.nrdiffusionvectors = 10000
	opt.nrofparticles = 1000
	opt.voxelsize = np.array([4,4,50])
	opt.maxiterations=10000
	opt.radius_nm = 100
	# opt.synexpandradiusxy_px = 6

	# Precompute diffusion vectors
	dvec = fibonacci_spiral_sphere(opt.nrdiffusionvectors)

	ds = opt.diffusionsteplength_nm/opt.voxelsize
	dvec = dvec*ds

	# Dilation params
	seg_se = ball(2)
	syn_se = np.zeros((10,10,3))
	syn_se[:,:,1] = 1

	# Synapse info
	syn_info_df = pd.read_csv(opt.syn_info)
	syn_id_list = np.array(syn_info_df["syn_id"])
	pre_id_list = np.array(syn_info_df["pre_id"])
	size_list = np.array(syn_info_df["size"])
	nsyn = syn_id_list.shape[0]

	errcount = 0

	out_syn_id = []
	out_pre_id = []
	out_post_id = []
	out_post_size = []
	# out_error = []
	for i in range(nsyn):

		syn_id = syn_id_list[i]
		pre_id = pre_id_list[i]
		syn_size = size_list[i]

		syn_mask = (syn_seg==syn_id).astype("uint8")
		x, y, z = np.where(syn_mask)
		syn_coord = np.c_[x,y,z]

		if x.shape[0] == 0:
			continue

		coord_max = np.max(syn_coord, axis=0)
		coord_min = np.min(syn_coord, axis=0)
		coord_max += np.ceil(opt.radius_nm/opt.voxelsize).astype("int")
		coord_min -= np.ceil(opt.radius_nm/opt.voxelsize).astype("int")

		coord_min = np.array([np.max([0, coord_min[i]]) for i in range(3)])
		coord_max = np.array([np.min([volsize[i], coord_max[i]]) for i in range(3)])

		syn_mask = syn_mask[coord_min[0]:coord_max[0],
												coord_min[1]:coord_max[1],
												coord_min[2]:coord_max[2]]
		seg_mask = seg[coord_min[0]:coord_max[0],
									 coord_min[1]:coord_max[1],
									 coord_min[2]:coord_max[2]]

		esyn = dilation(syn_mask, syn_se)
		pseg = (seg_mask==pre_id).astype("uint8")
		epseg = dilation(pseg, seg_se)
		
		exc_valid = (pseg!=0) + (esyn==0)
		epseg[exc_valid] = 0
		
		chunksize = np.array(epseg.shape)

		emittervoxels_loc = np.where(epseg)
		nemitvx = emittervoxels_loc[0].shape[0]

		if nemitvx==0:
		
			print(f"ERROR: Synapse with id {syn_id} has no emitter voxels!")
			errcount = errcount + 1
			# out_error.append(1)

		else:

			sourcevoxnr = np.random.randint(nemitvx, size=opt.nrofparticles)
			x = emittervoxels_loc[0][sourcevoxnr]
			y = emittervoxels_loc[1][sourcevoxnr]
			z = emittervoxels_loc[2][sourcevoxnr]

			nt = np.zeros((opt.nrofparticles, 5))
			nt[:,0] = x
			nt[:,1] = y
			nt[:,2] = z
			nt[:,3] = np.zeros(opt.nrofparticles)
			nt[:,4] = np.ones(opt.nrofparticles)

			count = 0
			while (count<opt.maxiterations) and (np.sum(nt[:,4])>0):

				vnr = np.random.randint(opt.nrdiffusionvectors, size=opt.nrofparticles)
				v = dvec[vnr,:]*nt[:,4].reshape((-1,1))
				nt[:,:3] = nt[:,:3] + v

				p = np.round(nt[:,:3])
				minp = np.min(p, axis=0)
				maxp = np.max(p, axis=0)

				if (np.min(minp)<0) or np.max(maxp-chunksize)>=0:
					
					exited = (np.min(p, axis=1)<0) + (p[:,0]>=chunksize[0]) + (p[:,1]>=chunksize[1]) + (p[:,2]>=chunksize[2])
					nt[exited,:3] = nt[exited,:3] - v[exited,:] # move back
					nt[exited,4] = 0
					p = np.round(nt[:,:3])

				p = p.astype("int")
				vids = seg_mask[p[:,0], p[:,1], p[:,2]]

				# Particles which moved to the presynaptic side
				vids_pre = vids==pre_id
				nt[vids_pre,:3] = nt[vids_pre,:3]-v[vids_pre,:]
				vids[vids_pre] = 0

				sp = np.where(vids>0)[0]
				nt[sp, 3] = vids[sp]
				nt[sp, 4] = 0

				count += 1

			post_list, post_size = np.unique(nt[:,3], return_counts=True)
			# If there are unassigned neurotransmitters
			if post_list[0] == 0:
				post_list = post_list[1:]
				post_size = post_size[1:]

			post_filt = post_size>(opt.nrofparticles*0.01)
			post_list = post_list[post_filt]
			post_size = post_size[post_filt]

			# Compute proportion
			post_size = post_size/np.sum(post_size)

			for j in range(post_list.shape[0]):
				out_syn_id.append(int(syn_id))
				out_pre_id.append(int(pre_id))
				out_post_id.append(int(post_list[j]))
				out_post_size.append(post_size[j]*syn_size)
				# out_error.append(0)

		if (i+1)<=20 or (i+1)%100==0:
			print(f"{i+1}/{nsyn} assigned.")
			
	syn_info = {"syn_id": out_syn_id,
							"pre_id": out_pre_id,
							"post_id": out_post_id,
							"size": out_post_size}
	out_df = pd.DataFrame(data=syn_info)
	out_df.to_csv(opt.outpath, index=False)